# RoboSkiAgent

LangGraph-based agent-driven universal robot Skill Library. Provides flexible task planning and execution capabilities through intelligent agent orchestration of robot skills.

## Setup

### Prerequisites
- Python >= 3.10
- [LangSmith API Key](https://smith.langchain.com/settings) (free to sign up)

### Installation Steps

1. **Install LangGraph CLI**
   ```bash
   pip install -U "langgraph-cli[inmem]"
   ```

2. **Create app from template** (optional)
   ```bash
   langgraph new path/to/your/app --template new-langgraph-project-python
   ```

3. **Install dependencies**
   ```bash
   pip install -e .
   ```

4. **Configure environment variables**
   
   Create a `.env` file and add:
   ```
   LANGSMITH_API_KEY=lsv2_...
   ```

5. **Launch development server**
   ```bash
   langgraph dev
   ```
   
   Access Studio UI: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

## Testing

Test using Python SDK:
```python
from langgraph_sdk import get_client
import asyncio

client = get_client(url="http://localhost:2024")

async def main():
    async for chunk in client.runs.stream(
        None,
        "agent",
        input={"messages": [{"role": "human", "content": "Execute task"}]},
    ):
        print(f"Event: {chunk.event}")
        print(chunk.data)

asyncio.run(main())
```
