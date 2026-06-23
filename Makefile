.PHONY: build run-teacher run-learner test

build:
	docker build -t sfagent-bridge -f docker/Dockerfile .

run-teacher:
	docker compose -f compose/docker-compose.teacher.yml up -d

run-learner:
	docker compose -f compose/docker-compose.learner.yml up -d

logs:
	docker logs sfagent-teacher || docker logs sfagent-learner

test:
	bash -n install.sh
	bash -n install-learner.sh
	bash -n install-teacher.sh
	python3 -m py_compile src/bridge.py
	python3 -m py_compile src/setup_agent.py
