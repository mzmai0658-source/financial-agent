import os
import sys

import uvicorn
from loguru import logger


# Allow `python -m src.agent.main` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    logger.info(f"Starting financial chat agent API on {host}:{port}")
    uvicorn.run("src.api.main:app", host=host, port=port, reload=os.getenv("API_RELOAD", "0") == "1")


if __name__ == "__main__":
    main()
