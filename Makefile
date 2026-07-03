.RECIPEPREFIX := >
.PHONY: install lock lint format typecheck test check serve

install:
> pip install -r requirements-lock.txt && pip install -e . --no-deps

lock:
> pip freeze | grep -v gridlens > requirements-lock.txt

lint:
> ruff check .

format:
> ruff format .

typecheck:
> mypy src

test:
> pytest

check: lint typecheck test

serve:
> gridlens serve
