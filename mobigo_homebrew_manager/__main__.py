from mobigo_homebrew_manager.app import run
from mobigo_homebrew_manager.elevation import ensure_elevated


def main() -> None:
    if ensure_elevated():
        run()


if __name__ == "__main__":
    main()
