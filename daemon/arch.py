"""Host architecture detection, mapped to Docker/OCI platform names."""

import platform

# uname -m -> (docker architecture, variant)
ARCH_TO_DOCKER = {
    "aarch64": ("arm64", ""),
    "armv7l": ("arm", "v7"),
    "armv8l": ("arm", "v7"),
    "i686": ("386", ""),
    "x86_64": ("amd64", ""),
    "riscv64": ("riscv64", ""),
}


def host_platform():
    """Return (architecture, variant) for this host, as Docker names them."""
    machine = platform.machine().lower()
    return ARCH_TO_DOCKER.get(machine, (machine, ""))
