from .cli import main

# Guarded: preview workers are spawned processes, which import the main module again.
if __name__ == "__main__":
	raise SystemExit(main())
