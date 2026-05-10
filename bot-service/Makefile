.PHONY: typecheck test-l1 test-l2 test-all

typecheck:
	mypy --strict bot_service/

test-l1:
	pytest -m "l1" -x

test-l2:
	pytest -m "l2" -x

test-all:
	mypy --strict bot_service/ && pytest -m "l1 or l2" --tb=short
