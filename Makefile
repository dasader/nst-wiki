.PHONY: up down rebuild logs ps test

up:            ## 스택 기동 (백그라운드)
	docker compose up -d

down:          ## 스택 중지
	docker compose down

rebuild:       ## 최신 코드 pull 후 재빌드·재기동
	git pull
	docker compose up -d --build --force-recreate

logs:          ## 로그 팔로우 (make logs s=api 로 특정 서비스)
	docker compose logs -f $(s)

ps:            ## 컨테이너 상태
	docker compose ps

test:          ## DB 불필요 테스트 (전체는 CLAUDE.md 참고)
	docker run --rm -v "$(PWD)/api:/app" -w /app nst-wiki-api:latest \
		sh -c "pip install -q pytest && python -m pytest -q"
