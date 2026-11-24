import os
import shutil
import subprocess

COHERENCE_IMAGE_ENV = "HOLON_COHERENCE_IMAGE"
LOCAL_COHERENCE_IMAGE = "holon-coherence:latest"
GHCR_COHERENCE_IMAGE = "ghcr.io/holon-agentic-coder/holon-coherence:latest"
COHERENCE_IMAGES = (LOCAL_COHERENCE_IMAGE, GHCR_COHERENCE_IMAGE)
_IMAGE_INSPECT_TIMEOUT_SECONDS = 2.0


def local_coherence_image() -> str | None:
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
    override = os.getenv(COHERENCE_IMAGE_ENV, "").strip()
    if override:
        return override
    return local_coherence_image() or GHCR_COHERENCE_IMAGE
