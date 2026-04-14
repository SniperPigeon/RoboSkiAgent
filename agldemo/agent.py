import agentlightning as agl
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
 
# 改成你本地已经 pull 的模型，例如 qwen2.5:7b、llama3.2:3b 等
OLLAMA_MODEL = "qwen3:8b"
 
 
@agl.rollout
def sentiment_agent(task: dict, prompt_template: agl.PromptTemplate) -> float:
    """
    执行一次 rollout：
    - task: {"input": str, "label": str}
    - prompt_template: APO 动态注入并优化的模板
    - 返回 reward: 0.0 或 1.0
    """
    formatted_prompt = prompt_template.format(input=task["input"])
 
    llm = ChatOllama(model=OLLAMA_MODEL, temperature=0)
    response = llm.invoke([HumanMessage(content=formatted_prompt)])
 
    output = response.content.strip().lower()
    label = task["label"].lower()
 
    reward = 1.0 if label in output else 0.0
    return reward
 