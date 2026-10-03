import os
import tomllib
from pathlib import Path

TOKEN_ENV = "MIRO_ACCESS_TOKEN"
DEFAULT_CONFIG = "config.toml"
# Next to pyproject.toml, so ./miro-export finds the token from any working directory.
PROJECT_CONFIG = Path(__file__).resolve().parent.parent / DEFAULT_CONFIG


class ConfigError(Exception):
	pass


def load_token(cli_token=None, config_path=None):
	"""Resolve the access token: --token > $MIRO_ACCESS_TOKEN > config.toml [miro] access_token."""
	if cli_token:
		return cli_token.strip()
	env_token = os.environ.get(TOKEN_ENV, "").strip()
	if env_token:
		return env_token
	candidates = [Path(config_path)] if config_path else [Path(DEFAULT_CONFIG), PROJECT_CONFIG]
	for path in candidates:
		if not path.is_file():
			continue
		with open(path, "rb") as f:
			config = tomllib.load(f)
		token = str((config.get("miro") or {}).get("access_token") or "").strip()
		if token:
			return token
	raise ConfigError(
		f"No Miro access token found. Set ${TOKEN_ENV}, pass --token, "
		f"or copy config.example.toml to {candidates[0]} and fill in [miro] access_token."
	)
