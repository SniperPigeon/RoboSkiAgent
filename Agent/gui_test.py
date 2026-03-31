import gradio as gr

MAX_BUTTONS = 4

def start_flow(prompt):
    options = ["✅ 批准", "❌ 拒绝", "🔍 更多信息"]
    log = f"[ 输入 ] {prompt}\n[ 节点1 ] 开始分析...\n[ 节点1 ] 检测到异常...\n⏸ 等待人工确认...\n"

    btn_updates = []
    for i in range(MAX_BUTTONS):
        if i < len(options):
            btn_updates.append(gr.update(value=options[i], visible=True))
        else:
            btn_updates.append(gr.update(visible=False))

    return [log] + btn_updates

def handle_choice(choice, current_log):
    new_log = current_log + f"[ 用户 ] 选择了：{choice}\n[ 节点2 ] 继续执行...\n[ 完成 ]\n"
    hide_all = [gr.update(visible=False)] * MAX_BUTTONS
    return [new_log] + hide_all

with gr.Blocks(title="HITL Demo") as demo:

    log_box = gr.Textbox(
        label="运行日志",
        lines=15,
        interactive=False
    )

    with gr.Row():
        buttons = [gr.Button(visible=False) for _ in range(MAX_BUTTONS)]

    # 输入区：prompt 框 + 按钮横排
    with gr.Row():
        prompt_box = gr.Textbox(
            placeholder="输入任务描述...",
            show_label=False,
            scale=4        # 占 4/5 的宽度
        )
        start_btn = gr.Button("🚀 开始", variant="primary", scale=1)

    start_btn.click(
        fn=start_flow,
        inputs=[prompt_box],      # ← 传入 prompt
        outputs=[log_box] + buttons
    )

    # 支持回车提交
    prompt_box.submit(
        fn=start_flow,
        inputs=[prompt_box],
        outputs=[log_box] + buttons
    )

    for btn in buttons:
        btn.click(
            fn=handle_choice,
            inputs=[btn, log_box],
            outputs=[log_box] + buttons
        )

demo.launch()