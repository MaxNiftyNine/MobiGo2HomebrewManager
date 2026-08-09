from mobigo_homebrew_manager.app import run
from mobigo_homebrew_manager.elevation import require_elevated


def main() -> None:
    try:
        require_elevated()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    run()


if __name__ == "__main__":
    main()
