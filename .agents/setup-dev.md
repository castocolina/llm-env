# Setup & Platform Rules

## Shell Script Validation

Every `.sh` edit must pass `make validate` (shellcheck) before committing. No exceptions.

## Platform Compatibility

`setup-dev.sh` supports Bazzite (Linux) and macOS. When modifying it:

### Use cross-platform helpers

- `get_file_size` - not `stat -c%s` or `stat -f%z` directly
- `get_cpu_cores` - not `nproc` or `sysctl -n hw.ncpu` directly
- `download_file` - not `wget` or `curl` directly

### Platform paths

| Platform | Container | Package Manager | GPU API |
|----------|-----------|-----------------|---------|
| Linux | distrobox | dnf5 | Vulkan (`-DGGML_VULKAN=ON`) |
| macOS | native | brew | Metal (`-DGGML_METAL=ON`) |

### Detection

Use `detect_os()` which returns `linux` or `macos`. Never hardcode platform checks.

## Research Before Implementation

Platform-specific tools have non-obvious requirements that cause silent failures. Always research before assuming behavior.

Examples of unresearched assumptions that break things:
- HuggingFace public model downloads work without auth tokens
- `stat -c%s` silently fails on macOS (use `stat -f%z`)
- `nproc` does not exist on macOS (use `sysctl -n hw.ncpu`)
- Vulkan drivers may need `render` group membership
- Metal requires Xcode command line tools installed

If a platform behavior is not 100% clear, research it or ask the user before implementing.
