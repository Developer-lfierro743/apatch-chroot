"""Docker Hub / OCI registry client: ref parsing, auth, manifest, layers.

A lean implementation of the OCI Distribution spec v2 flow that chroot-distro
uses, trimmed to what apatch-chroot needs (public pulls from Docker Hub and
any registry that follows the token dance). Runs in the daemon, as root.

Flow:
  1. parse_image_ref   -> (registry, repo, tag)
  2. get_token         -> Bearer token (Docker Hub fixed endpoint; others 401-dance)
  3. get_manifest      -> manifest or manifest-list, Content-Type wins
  4. pick_platform     -> narrow a manifest-list to this host's arch
  5. get_blob          -> stream one layer (tar.gz) to disk
  6. apply_layer       -> extract a layer into the rootfs, in order
"""

import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
import urllib.error
import urllib.request

REGISTRY_URL = "https://registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"
UA = "apatch-chroot/0.1"

# The four manifest media types a registry may answer with. Asking for all of
# them lets the server pick; Content-Type on the response is what we trust.
ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
])

ARCH_TO_DOCKER = {
    "aarch64": ("arm64", ""),
    "arm": ("arm", "v7"),
    "i686": ("386", ""),
    "x86_64": ("amd64", ""),
    "riscv64": ("riscv64", ""),
}


class RegistryError(Exception):
    pass


def parse_image_ref(image_ref):
    """Parse into (registry, repo, tag) using Docker's own rule.

    A first component with a '.' or ':' is a registry host; otherwise it is part
    of the repository. Bare names get 'library/' (Docker Hub official images).
    'docker.io'/'index.docker.io' normalise to '' so Docker Hub has one spelling.
    """
    parts = image_ref.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0]):
        registry, remainder = parts
    else:
        registry, remainder = "", image_ref

    if registry in ("docker.io", "index.docker.io"):
        registry = ""

    if ":" in remainder:
        name, tag = remainder.rsplit(":", 1)
    else:
        name, tag = remainder, "latest"

    repo = name if (registry or "/" in name) else f"library/{name}"
    return registry, repo, tag


def derive_alias(image_ref):
    """Short local container name from an image ref ('ubuntu:24.04' -> 'ubuntu')."""
    _reg, repo, _tag = parse_image_ref(image_ref)
    return repo.split("/")[-1]


def _base_url(registry):
    return f"https://{registry}" if registry else REGISTRY_URL


def _auth_url(registry, repo):
    if not registry:
        return f"{AUTH_URL}?service=registry.docker.io&scope=repository:{repo}:pull"
    # The generic dance: probe /v2/, read the WWW-Authenticate Bearer realm.
    base = _base_url(registry)
    req = urllib.request.Request(f"{base}/v2/", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            # A registry that needs no auth answers 200 here.
            if resp.status == 200:
                return None
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise RegistryError(f"registry {registry} /v2/ probe failed: {e.code}")
        challenge = e.headers.get("WWW-Authenticate", "")
        realm = _parse_realm(challenge)
        if not realm:
            return None  # no Bearer challenge -> no token needed
        return realm
    except urllib.error.URLError as e:
        raise RegistryError(f"cannot reach registry {registry}: {e}") from e
    return None


def _parse_realm(challenge):
    """Pull realm, service, scope out of a 'Bearer realm=...,service=...' header."""
    if not challenge.lower().startswith("bearer"):
        return ""
    params = {}
    for part in challenge.split(" ", 1)[1].split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip().strip('"')
    if "realm" not in params:
        return ""
    from urllib.parse import urlencode
    q = {"service": params.get("service", "")}
    if "scope" in params:
        q["scope"] = params["scope"]
    sep = "&" if "?" in params["realm"] else "?"
    return f"{params['realm']}{sep}{urlencode(q)}"


def get_token(registry, repo):
    """Return a Bearer token string, or '' when the registry needs none."""
    url = _auth_url(registry, repo)
    if url is None:
        return ""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("token", "") or data.get("access_token", "")
    except (urllib.error.URLError, ValueError) as e:
        raise RegistryError(f"auth failed for {repo}: {e}") from e


def _headers(token):
    h = {"User-Agent": UA, "Accept": ACCEPT}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_manifest(registry, repo, ref, token):
    """Fetch the manifest (or index) for repo:ref. Returns (dict, content_type).

    The response Content-Type decides which shape arrived; it wins over the
    body's own mediaType. A digest of the raw bytes rides along as '_digest'.
    """
    url = f"{_base_url(registry)}/v2/{repo}/manifests/{ref}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RegistryError(f"access denied pulling {repo}:{ref} (need credentials?)")
        if e.code == 404:
            raise RegistryError(f"{repo}:{ref} not found in registry")
        raise RegistryError(f"manifest fetch failed: {e.code}") from e
    except urllib.error.URLError as e:
        raise RegistryError(f"manifest fetch failed: {e}") from e

    data = json.loads(body)
    data["_ct"] = ct.split(";")[0].strip() or data.get("mediaType", "")
    data["_digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
    return data, data["_ct"]


def is_index(ct):
    return ct in (
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    )


def pick_platform(manifest, arch, variant, image_ref):
    """From a manifest-list, return the entry matching this host's arch."""
    entries = manifest.get("manifests", [])
    for e in entries:
        p = e.get("platform", {})
        if p.get("os", "linux") != "linux":
            continue
        if p.get("architecture") != arch:
            continue
        if variant and p.get("variant", "") not in (variant, ""):
            continue
        return e
    for e in entries:  # fallback: any linux entry for the arch
        p = e.get("platform", {})
        if p.get("os", "linux") == "linux" and p.get("architecture") == arch:
            return e
    have = ", ".join(
        f"{e.get('platform', {}).get('architecture', '?')}/{e.get('platform', {}).get('variant', '')}".rstrip("/")
        for e in entries if e.get("platform", {}).get("os", "linux") == "linux"
    )
    raise RegistryError(f"no {arch} image for '{image_ref}'; available: {have}")


def get_blob(registry, repo, digest, token, dest_path, on_progress=None):
    """Stream one blob (layer) to dest_path. Returns the sha256 of the bytes."""
    url = f"{_base_url(registry)}/v2/{repo}/blobs/{digest}"
    req = urllib.request.Request(url, headers=_headers(token))
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                if on_progress:
                    on_progress(len(chunk))
    except urllib.error.HTTPError as e:
        raise RegistryError(f"blob {digest} fetch failed: {e.code}") from e
    except urllib.error.URLError as e:
        raise RegistryError(f"blob {digest} fetch failed: {e}") from e
    return h.hexdigest()
