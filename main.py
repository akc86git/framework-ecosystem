import asyncio
import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import httpx

# для печати таблицы в терминал
from rich.console import Console
from rich.table import Table

API = 'https://api.github.com/repos/'
REPOS = ['langchain-ai/langgraph','crewAIInc/crewAI','microsoft/autogen',
         'ag2ai/ag2','ollama/ollama','modelcontextprotocol/servers',
         'langfuse/langfuse','qdrant/qdrant','openai/openai-agents-python'
        ]

RETRYABLE_STATUSES = {403, 429, 500, 502, 503, 504}

@dataclass
class RepoStats():
    info_date: datetime
    full_name: str
    created_at: datetime
    pushed_at: datetime
    stars: int
    forks: int
    open_issues_count: int
    age_days: int = field(init=False)
    days_since_push: int = field(init=False)
    stars_per_month: float = field(init=False)   # stars / (возраст в месяцах, минимум 1)
    open_issues_to_stars: float = field(init=False)

    def __post_init__(self):
        created_at = datetime.fromisoformat(self.created_at)
        self.age_days = (self.info_date - created_at).days
        self.days_since_push = (self.info_date - datetime.fromisoformat(self.pushed_at)).days

        full_months_age = (self.info_date.year - created_at.year) * 12 + (self.info_date.month - created_at.month)
        self.stars_per_month = round(self.stars / max(full_months_age, 1), 1)
        self.open_issues_to_stars = round(self.open_issues_count / self.stars, 5) if self.stars > 0 else 0


class RateLimitError(Exception):
    """Квота исчерпана; несём с собой время ожидания."""
    def __init__(self, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(f"Превышен лимит подключений к GitHub, попробуйте через {retry_after} сек")


async def fetch_repo(client: httpx.AsyncClient, repo: str) -> dict:
    headers = {'Accept': 'application/vnd.github+json'}
    dt_now = datetime.now(timezone.utc)
    resp = await client.get(API + repo, headers=headers)
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        raise RateLimitError(retry_after=None)  # следующий запрос будет отклонён
    resp.raise_for_status()
    resp_with_date = resp.json()
    resp_with_date['info_date'] = dt_now
    return resp_with_date



async def fetch_with_retry(client: httpx.AsyncClient, repo: str,
                           retries: int = 3) -> dict:
    for attempt in range(retries + 1):
        try:
            return await fetch_repo(client, repo)
        except RateLimitError as exc:
            if attempt == retries:
                raise
            wait = exc.retry_after or 60.0
        except httpx.HTTPStatusError as exc:
            if attempt == retries or exc.response.status_code not in RETRYABLE_STATUSES:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else 2.0 ** attempt
        except httpx.TransportError:
            if attempt == retries:
                raise
            wait = 2.0 ** attempt

        await asyncio.sleep(wait)   # именно asyncio.sleep, не time.sleep!
    raise RuntimeError("unreachable")


def build_stats(payload: dict) -> RepoStats:
    return RepoStats(
        payload['info_date'], 
        payload['full_name'], 
        payload['created_at'], 
        payload['pushed_at'], 
        payload['stargazers_count'], 
        payload['forks'], 
        payload['open_issues']
    )


async def main() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        # gather по REPOS -> stats -> CSV + таблица
        tasks = [fetch_with_retry(client, repo) for repo in REPOS]
        async with asyncio.Semaphore(5) as semaphore:
            results = await asyncio.gather(*tasks)

    stats = [build_stats(result) for result in results]
    fieldnames = list(stats[0].__dict__.keys())

    with open('repos_raw.csv', 'r', newline='', encoding='utf-8-sig') as f:
        has_rows = any(any(c.strip() for c in r) for r in csv.reader(f))

    with open('repos_raw.csv', 'a+', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not has_rows:
            writer.writeheader()
        writer.writerows(asdict(st) for st in stats)   

    sorted_stats = sorted(stats, key=lambda x: x.stars_per_month, reverse=True)       

    # печать таблицы в консоль
    console = Console()
    table = Table(title="Репозитории", show_lines=True)

    for h in sorted_stats[0].__dict__.keys():                     # <- одна строка на все столбцы
        table.add_column(h)

    for r in sorted_stats:
        table.add_row(*(str(v) for v in r.__dict__.values()))

    console.print(table) 

    # печать аналитического вывода в файл
    comment = f"""Результат анализа на дату {sorted_stats[0].info_date.strftime("%Y-%m-%d %H:%M:%S")}:
            Наиболее высокий рост показывает репозиторий {sorted_stats[0].full_name}:
            Среднее количество звёзд за месяц: {sorted_stats[0].stars_per_month}
            Доля открытых проблем к звездам: {sorted_stats[0].open_issues_to_stars}"""
    comment += '\n\n'
    with open("README.md", "a", encoding="utf-8") as f:
        f.write(comment) 


if __name__ == "__main__":
    asyncio.run(main())