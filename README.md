# RoboSkiAgent

基于LangGraph的Agent驱动机器人通用Skill Library。通过智能代理编排和执行机器人技能，提供灵活的任务规划和执行能力。

## Setup

### 前置要求
- Python >= 3.10
- [LangSmith API Key](https://smith.langchain.com/settings) (免费注册)

### 安装步骤

1. **安装LangGraph CLI**
   ```bash
   pip install -U "langgraph-cli[inmem]"
   ```

2. **从模板创建应用** (可选)
   ```bash
   langgraph new path/to/your/app --template new-langgraph-project-python
   ```

3. **安装依赖**
   ```bash
   pip install -e .
   ```

4. **配置环境变量**
   
   创建 `.env` 文件并添加：
   ```
   LANGSMITH_API_KEY=lsv2_...
   ```

5. **启动开发服务器**
   ```bash
   langgraph dev
   ```
   
   访问 Studio UI: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

## 测试

使用Python SDK测试：
```python
from langgraph_sdk import get_client
import asyncio

client = get_client(url="http://localhost:2024")

async def main():
    async for chunk in client.runs.stream(
        None,
        "agent",
        input={"messages": [{"role": "human", "content": "执行任务"}]},
    ):
        print(f"Event: {chunk.event}")
        print(chunk.data)

asyncio.run(main())
```
