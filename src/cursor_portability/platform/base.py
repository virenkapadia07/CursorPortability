from __future__ import annotations

from cursor_portability.platform.env import Environment
from cursor_portability.platform.types import PlatformPaths


def get_platform_paths(env: Environment) -> PlatformPaths:
    if env.platform == "darwin":
        from cursor_portability.platform import macos as macos_mod

        return macos_mod.resolve_paths(env)
    if env.platform == "win32":
        from cursor_portability.platform import windows as windows_mod

        return windows_mod.resolve_paths(env)
    if env.platform.startswith("linux") or env.os_name == "posix":
        from cursor_portability.platform import linux as linux_mod
        from cursor_portability.platform import wsl as wsl_mod

        base = linux_mod.resolve_paths(env)
        if env.wsl_distro_name:
            wsl_extra = wsl_mod.windows_side_cursor_home(env)
            return PlatformPaths(
                user_data_dir=base.user_data_dir,
                cursor_home=base.cursor_home,
                app_install_candidates=base.app_install_candidates,
                wsl_windows_cursor_home=wsl_extra,
                notes=(*base.notes, "WSL: Windows-side store listed separately"),
            )
        return base
    return PlatformPaths(None, None, (), notes=(f"unsupported platform: {env.platform}",))
