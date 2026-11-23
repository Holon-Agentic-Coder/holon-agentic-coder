"""Which holon-coherence proxy image the token-reduce sidecar runs.

holon-coherence owns token reduction: its image ships the mitmproxy addon and starts it with
``holon-coherence start``, so this repository mounts no addon or source tree into the sidecar.
"""

import os
import shutil
import subprocess

#: Environment variable that pins the sidecar to an explicit image (a tag or digest).
COHERENCE_IMAGE_ENV = "HOLON_COHERENCE_IMAGE"
#: The image ``holon-coherence build`` tags locally.
LOCAL_COHERENCE_IMAGE = "holon-coherence:latest"
#: The published image; ``docker run`` pulls it when no local image exists.
GHCR_COHERENCE_IMAGE = "ghcr.io/holon-agentic-coder/holon-coherence:latest"
# Probed in this order: a locally built image wins over a previously pulled one.
COHERENCE_IMAGES = (LOCAL_COHERENCE_IMAGE, GHCR_COHERENCE_IMAGE)
_IMAGE_INSPECT_TIMEOUT_SECONDS = 2.0


def local_coherence_image() -> str | None:
    """The first holon-coherence image present locally per ``docker image inspect``, or None.

    Returns None when docker is not on PATH or neither image exists; inspect errors and timeouts count as absent.
    """
    if not shutil.which("docker"):
        return None
    for image in COHERENCE_IMAGES:
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                text=True,
                check=False,
                timeout=_IMAGE_INSPECT_TIMEOUT_SECONDS,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if proc.returncode == 0:
            return image
    return None


def coherence_proxy_image() -> str:
    """The image the sidecar starts: ``HOLON_COHERENCE_IMAGE`` when set, else a local image, else the GHCR image."""
    override = os.getenv(COHERENCE_IMAGE_ENV, "").strip()
    if override:
        return override
    return local_coherence_image() or GHCR_COHERENCE_IMAGE
