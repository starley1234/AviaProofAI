# AviaProofAI — команды разработки
.PHONY: install demo test serve stub sync audit conflicts traceability db

install:
	pip install -r requirements.txt

db:
	python -m app.cli init-db

stub:
	uvicorn stub_tc.server:app --port 9080

serve:
	python -m app.cli serve

sync:
	python -m app.cli sync

audit:
	python -m app.cli audit

conflicts:
	python -m app.cli conflicts

traceability:
	python -m app.cli traceability

test:
	python -m pytest tests/ -q

demo: db
	bash scripts/demo.sh
