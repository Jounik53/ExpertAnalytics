from app.bootstrap import build_app
from app.adapters.persistence.settings_repo import JsonSettingsRepository
from app.application.helpers.modes import normalize_mode
from app.application.helpers.windows_elevation import is_admin, relaunch_as_admin
from app.application.services.settings_service import SettingsService
import os
from pathlib import Path
from app.ui.startup_mode_dialog import choose_start_mode


def main() -> None:
    if os.name == "nt" and not is_admin():
        if relaunch_as_admin():
            return
    default_mode, open_selected_immediately = _load_startup_preferences()
    current_mode = default_mode if open_selected_immediately else choose_start_mode(default_mode)
    pending_mode: dict[str, str | None] = {"next": None}

    while True:
        pending_mode["next"] = None
        app = build_app(mode=current_mode)
        app.set_mode_switch_callback(lambda next_mode: pending_mode.__setitem__("next", next_mode))
        app.run()
        if pending_mode["next"]:
            current_mode = str(pending_mode["next"])
            continue
        break


def _load_startup_preferences() -> tuple[str, bool]:
    data_dir = Path(__file__).resolve().parent / "app" / "data"
    settings = SettingsService(JsonSettingsRepository(data_dir / "settings.json")).load()
    return (
        normalize_mode(getattr(settings, "startup_mode", "general")),
        bool(getattr(settings, "open_selected_mode_on_startup", False)),
    )


if __name__ == "__main__":
    main()
