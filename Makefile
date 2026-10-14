.PHONY: build test local local-fastapi

build:
	docker run --rm --entrypoint /bin/bash -v "$(PWD)":/var/task -w /var/task public.ecr.aws/lambda/python:3.12 -lc 'rm -rf build/package build/media-service.zip && mkdir -p build/package && pip install -r requirements.txt -t build/package && cp -R src/*.py build/package/ && cp -R src/static build/package/ && cd build/package && python -c "import shutil; shutil.make_archive(\"../media-service\", \"zip\", \".\")"'

test:
	.venv/bin/pytest -q

local:
	RUN_LOCAL_API=false bash scripts/start-local.sh

local-fastapi:
	RUN_LOCAL_API=true bash scripts/start-local.sh
