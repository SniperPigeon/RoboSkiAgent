import os
import agentlightning as agl
from openai import AsyncOpenAI

from agent import sentiment_agent
from trainset import TRAIN_DATA, VAL_DATA


def make_initial_prompt() -> agl.PromptTemplate:
    return agl.PromptTemplate(
        template="Classify the sentiment of the text as positive or negative.\n\nText: {input}",
        engine="f-string",
    )


def main():
    # Critic: Gemini via OpenAI-compatible endpoint，无需任何中间件
    # API key 从 https://aistudio.google.com 获取
    critic_client = AsyncOpenAI(
        api_key=os.environ["GEMINI_API_KEY"],
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    algo = agl.APO(
        critic_client,
        gradient_model="gemini-2.5-flash",   # critique 生成
        apply_edit_model="gemini-2.5-flash",  # prompt 编辑
        val_batch_size=4,
        gradient_batch_size=2,
        beam_width=2,
        branch_factor=2,
        beam_rounds=2,
    )

    trainer = agl.Trainer(
        algorithm=algo,
        n_runners=2,
        initial_resources={"prompt_template": make_initial_prompt()},
        adapter=agl.TraceToMessages(),
    )

    trainer.fit(
        agent=sentiment_agent,
        train_dataset=TRAIN_DATA,
        val_dataset=VAL_DATA,
    )

    best_prompt = algo.get_best_prompt()
    print("\n=== 优化后的最佳 Prompt ===")
    print(best_prompt.template)


if __name__ == "__main__":
    main()