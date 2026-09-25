# macOS GitHub Actions Runners and Docker Availability

`macos-latest` runners in GitHub Actions do not support nested virtualization. Consequently, container runtimes such as
Docker Desktop, Colima, or Podman cannot run containers on standard macOS runners.

Because of this architectural constraint, all container builds and integration test suites that depend on Docker
services are explicitly restricted to `ubuntu-latest` Linux runners in our CI workflows.

### References & Background:

- [GitHub Community Discussion #160591: Docker on macOS runners](https://github.com/orgs/community/discussions/160591)
- [GitHub Actions Runner Images: macOS 15 arm64 Readme](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-arm64-Readme.md)
- [GitHub Actions Runner Issue #12933](https://github.com/actions/runner-images/issues/12933)
