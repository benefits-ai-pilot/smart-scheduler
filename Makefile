install:
	@echo "-> Configuring uv package manager"
ifndef UV_EXISTS
	curl -LsSf https://astral.sh/uv/install.sh | sh
endif
	uv venv --python 3.13

dev: install
	@echo "-> Installing Developer Dependencies"
	uv sync
	uvx pre-commit install

format:
	@echo "Formatting code..."
	uvx ruff format .
	uvx ruff check --fix . || true