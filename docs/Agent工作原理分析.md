# Strands Agent 工作原理详细分析

> 基于 `strands-agent` SDK 源码分析

## 目录

1. [核心架构总览](#1-核心架构总览)
2. [初始化流程](#2-初始化流程)
3. [调用入口点](#3-调用入口点)
4. [核心事件循环](#4-核心事件循环)
5. [模型执行](#5-模型执行)
6. [工具执行](#6-工具执行)
7. [工具注册系统](#7-工具注册系统)
8. [对话管理器系统](#8-对话管理器系统)
9. [钩子系统](#9-钩子系统)
10. [中间件系统](#10-中间件系统)
11. [重试策略](#11-重试策略)
12. [直接工具调用](#12-直接工具调用)
13. [完整执行流程图](#13-完整执行流程图)
14. [关键类图](#14-关键类图)
15. [总结](#15-总结)

---

## 1. 核心架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Agent (主入口)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  组件:                                                                    │
│  ├── model: Model               # 语言模型 (BedrockModel/其他)              │
│  ├── tool_registry: ToolRegistry # 工具注册表                               │
│  ├── conversation_manager        # 对话管理器 (滑动窗口/总结)                │
│  ├── hooks: HookRegistry        # 钩子系统                                  │
│  ├── _middleware_registry       # 中间件系统                               │
│  ├── tool_executor              # 工具执行器 (并发/顺序)                   │
│  ├── _retry_strategy            # 重试策略                                 │
│  └── messages: Messages         # 对话历史                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 主要文件对应关系

| 文件 | 职责 |
|------|------|
| `agent.py` | Agent 主类，入口点和初始化 |
| `event_loop.py` | 事件循环核心逻辑 |
| `streaming.py` | 模型流处理 |
| `tools/registry.py` | 工具注册表 |
| `tools/_caller.py` | 直接工具调用器 |
| `tools/executors/_executor.py` | 工具执行器基类 |
| `tools/executors/concurrent.py` | 并发工具执行器 |
| `conversation_manager/` | 对话管理器实现 |
| `hooks/registry.py` | 钩子系统 |
| `_middleware/registry.py` | 中间件注册系统 |
| `_middleware/stages.py` | 中间件阶段定义 |
| `event_loop/_retry.py` | 重试策略 |

---

## 2. 初始化流程

源码位置: `agent.py` 第 158-407 行

### 2.1 完整初始化代码流程

```python
def __init__(self, model, messages, tools, system_prompt, ...):
    # 1. 模型解析
    if isinstance(model, ModelRouter):
        self._model_router = model
        self.model = model.default_model
    elif not model:
        self.model = BedrockModel()  # 默认使用 Bedrock
    elif isinstance(model, str):
        self.model = BedrockModel(model_id=model)
    else:
        self.model = model
    
    # 2. 消息初始化
    self.messages = messages if messages is not None else []
    
    # 3. 沙箱环境
    self._sandbox = sandbox or NotASandboxLocalEnvironment()
    
    # 4. 系统提示词处理
    self._system_prompt, self._system_prompt_content = split_system_prompt(system_prompt)
    
    # 5. 回调处理器
    if isinstance(callback_handler, _DefaultCallbackHandlerSentinel):
        self.callback_handler = PrintingCallbackHandler()
    elif callback_handler is None:
        self.callback_handler = null_callback_handler
    else:
        self.callback_handler = callback_handler
    
    # 6. 对话管理器解析
    resolved_conversation_manager, resolved_plugins = self._resolve_context_manager(...)
    
    if self.model.stateful:
        self.conversation_manager = NullConversationManager()
    elif resolved_conversation_manager:
        self.conversation_manager = resolved_conversation_manager
    elif conversation_manager:
        self.conversation_manager = conversation_manager
    else:
        self.conversation_manager = SlidingWindowConversationManager()
    
    # 7. 工具注册表初始化
    self.tool_registry = ToolRegistry()
    
    # 8. 工具处理
    if tools is not None:
        self.tool_registry.process_tools(tools)
    
    # 9. Agentic 模式工具注入
    if context_manager == "agentic":
        self.tool_registry.process_tools([summarize_context, truncate_context, pin_context])
    
    # 10. 工具初始化
    self.tool_registry.initialize_tools(self.load_tools_from_directory)
    
    # 11. 沙箱工具注入
    for sandbox_tool in self._sandbox.get_tools():
        if sandbox_tool.tool_name not in self.tool_registry.registry:
            self.tool_registry.register_tool(sandbox_tool)
    
    # 12. 钩子系统初始化
    self.hooks = HookRegistry()
    
    # 13. 中间件注册表
    self._middleware_registry = MiddlewareRegistry()
    self._plugin_registry = _PluginRegistry(self)
    
    # 14. 模型路由插件
    if self._model_router is not None:
        self._plugin_registry.add_and_init(self._model_router)
    
    # 15. Agentic 模式中间件
    if context_manager == "agentic":
        self._middleware_registry.add_middleware(InvokeModelStage.Input, create_token_usage_middleware())
    
    # 16. 并发控制器
    self._concurrency = _ConcurrencyController(concurrent_invocation_mode)
    
    # 17. 重试策略
    if isinstance(retry_strategy, _DefaultRetryStrategySentinel):
        self._retry_strategy = ModelRetryStrategy(
            max_attempts=6, max_delay=240, initial_delay=4
        )
    elif retry_strategy is None:
        self._retry_strategy = ModelRetryStrategy(max_attempts=1)
    else:
        self._retry_strategy = retry_strategy
    
    # 18. 注册钩子
    self.hooks.add_hook(self.conversation_manager)
    self.hooks.add_hook(self._retry_strategy)
    
    # 19. 工具执行器
    self.tool_executor = tool_executor or ConcurrentToolExecutor()
    
    # 20. 用户钩子
    if hooks:
        for hook in hooks:
            if isinstance(hook, HookProvider):
                self.hooks.add_hook(hook)
            elif callable(hook):
                self.hooks.add_callback(None, hook)
    
    # 21. 干预处理器
    self._intervention_registry = InterventionRegistry(interventions or [], self.hooks)
    
    # 22. 插件注册
    self._plugin_registry.add_and_init(_ModelPlugin())
    
    # 23. Agent 委托插件
    if not has_agent_delegation:
        self._plugin_registry.add_and_init(AgentDelegation())
    
    # 24. 内存管理器
    self.memory_manager = self._resolve_memory_manager(memory_manager)
    if self.memory_manager is not None:
        self._plugin_registry.add_and_init(self.memory_manager)
    
    # 25. 触发初始化完成事件
    self.hooks.invoke_callbacks(AgentInitializedEvent(agent=self))
```

### 2.2 上下文管理器解析

```python
@staticmethod
def _resolve_context_manager(
    context_manager: "ContextManagerStrategy | None",
    conversation_manager: ConversationManager | None,
    plugins: list[Plugin] | None,
) -> tuple[ConversationManager | None, list[Plugin] | None]:
    """解析 context_manager 策略"""

    if context_manager is None:
        return None, None

    if context_manager == "auto":
        # 自动模式: 上下文卸载器 + 总结管理器
        offloader = ContextOffloader(
            max_result_tokens=1500,
            preview_tokens=750,
        )
        default_cm = SummarizingConversationManager(
            summary_ratio=0.3, proactive_compression={"compression_threshold": 0.85}
        )
    elif context_manager == "agentic":
        # 代理模式: 模型驱动上下文管理
        offloader = ContextOffloader(
            max_result_tokens=8000,
            preview_tokens=750,
        )
        default_cm = SummarizingConversationManager(summary_ratio=0.3)

    resolved_plugins = list(plugins) if plugins else []
    if not has_offloader:
        resolved_plugins.append(offloader)

    resolved_cm = conversation_manager if conversation_manager else default_cm

    return resolved_cm, resolved_plugins
```

---

## 3. 调用入口点

### 3.1 同步调用 `__call__`

```python
def __call__(
    self,
    prompt: AgentInput = None,
    *,
    invocation_state: dict[str, Any] | None = None,
    structured_output_model: type[BaseModel] | None = None,
    structured_output_prompt: str | None = None,
    idempotency_token: Any = None,
    limits: Limits | None = None,
    **kwargs: Any,
) -> AgentResult:
    """处理自然语言提示"""
    return run_async(lambda: self._invoke_async_and_flush(prompt, **kwargs))
```

### 3.2 异步调用 `invoke_async`

```python
async def invoke_async(self, prompt, ...) -> AgentResult:
    """异步入口，收集所有事件，返回最终结果"""
    events = self.stream_async(prompt, ...)
    async for event in events:
        _ = event
    return cast(AgentResult, event["result"])
```

### 3.3 流式调用 `stream_async`

```python
async def stream_async(
    self,
    prompt: AgentInput = None,
    *,
    invocation_state: dict[str, Any] | None = None,
    structured_output_model: type[BaseModel] | None = None,
    ...
) -> AsyncIterator[Any]:
    """流式调用入口"""
    
    # 1. 验证限制
    self._validate_limits(limits)
    
    # 2. 并发控制
    begin = self._concurrency.begin(idempotency_token)
    
    if begin.waiting_on is not None:
        # 幂等性token去重
        await begin.waiting_on.register_waiter()
        if begin.waiting_on.result is not None:
            dup_callback_handler(...)
            yield AgentResultEvent(result=dup_result).as_dict()
        return
    
    if not begin.lock_acquired:
        raise ConcurrencyException(...)
    
    # 3. 中断恢复
    self._interrupt_state.resume(prompt)
    
    # 4. 重置指标
    self.event_loop_metrics.reset_usage_metrics()
    
    # 5. 重置检查点状态
    self._checkpoint_cycle_index = 0
    self._checkpoint_resume_position = None
    
    # 6. 合并状态
    merged_state = {}
    if kwargs:
        warnings.warn("`**kwargs` is deprecated, use `invocation_state` instead.")
        merged_state.update(kwargs)
        if invocation_state is not None:
            merged_state["invocation_state"] = invocation_state
    
    # 7. 回调处理器
    callback_handler = self.callback_handler
    if kwargs:
        callback_handler = kwargs.get("callback_handler", self.callback_handler)
    
    # 8. 消息转换
    messages = await self._convert_prompt_to_messages(prompt)
    
    # 9. 启动追踪
    self.trace_span = self._start_agent_trace_span(messages)
    
    # 10. 运行事件循环
    with trace_api.use_span(self.trace_span):
        try:
            events = self._run_loop(
                messages, merged_state, structured_output_model, ...
            )
            
            stop_event: EventLoopStopEvent | None = None
            async for event in events:
                event.prepare(invocation_state=merged_state)
                
                if isinstance(event, EventLoopStopEvent):
                    stop_event = event
                
                if event.is_callback_event:
                    callback_handler(**event.as_dict())
                    yield event.as_dict()
            
            if stop_event is None:
                raise RuntimeError("Agent stream produced no result event.")
            
            result = AgentResult(*stop_event["stop"])
            callback_handler(result=result)
            yield AgentResultEvent(result=result).as_dict()
            
        finally:
            self._cancel_signal.clear()
            self._concurrency.complete(begin.registered_token, result=result)
```

### 3.4 提示词转消息

```python
async def _convert_prompt_to_messages(self, prompt: AgentInput) -> Messages:
    """将各种格式的输入转换为消息列表"""
    
    if self._interrupt_state.activated:
        return []
    
    if self._try_consume_checkpoint_resume(prompt):
        return []
    
    messages = None
    
    if prompt is not None:
        # 检查最新消息是否是 toolUse
        if self.messages and any("toolUse" in content for content in self.messages[-1]["content"]):
            # 添加 toolResult 消息
            tool_use_ids = [content["toolUse"]["toolUseId"] ...]
            await self._append_messages({
                "role": "user",
                "content": generate_missing_tool_result_content(tool_use_ids),
            })
        
        if isinstance(prompt, str):
            # 字符串输入 → 用户消息
            messages = [{"role": "user", "content": [{"text": prompt}]}]
        
        elif isinstance(prompt, list):
            if len(prompt) == 0:
                messages = []
            elif all(isinstance(item, dict) for item in prompt):
                if all(all(key in item for key in Message.__required_keys__) for item in prompt):
                    # 消息列表输入
                    messages = cast(Messages, prompt)
                elif all(any(key in ContentBlock.__annotations__.keys() for key in item) ...):
                    # 内容块列表输入
                    messages = [{"role": "user", "content": cast(list[ContentBlock], prompt)}]
    
    if messages is None:
        raise ValueError("Invalid prompt type")
    
    return messages
```

---

## 4. 核心事件循环

### 4.1 主循环 `_run_loop`

源码位置: `agent.py` 第 1180-1280 行

```
┌──────────────────────────────────────────────────────────────────┐
│                      _run_loop (主循环)                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  while current_messages is not None:                              │
│      │                                                            │
│      ├── 1. BeforeInvocationEvent 钩子                           │
│      │                                                            │
│      ├── 2. 检查中断状态                                          │
│      │                                                            │
│      ├── 3. 初始化事件循环                                        │
│      │                                                            │
│      ├── 4. 调用中间件链                                          │
│      │     ┌─────────────────────────────────────┐               │
│      │     │  AgentStreamStage 中间件链            │               │
│      │     │  (可包含 Input/Wrap/Output 处理器)   │               │
│      │     └─────────────────────────────────────┘               │
│      │                                                            │
│      ├── 5. 处理 EventLoopStopEvent                               │
│      │                                                            │
│      ├── 6. AfterInvocationEvent 钩子                             │
│      │                                                            │
│      └── 7. 检查是否需要恢复 (resume)                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 事件循环周期 `event_loop_cycle`

源码位置: `event_loop.py` 第 146-298 行

```python
async def event_loop_cycle(
    agent: "Agent",
    invocation_state: dict[str, Any],
    structured_output_context: StructuredOutputContext | None = None,
    limits: Limits | None = None,
) -> AsyncGenerator[TypedEvent, None]:
    """执行单个事件循环周期"""
    
    # 1. 检查限制
    limit_stop_reason = _check_limits(agent, limits)
    if limit_stop_reason is not None:
        yield EventLoopStopEvent(limit_stop_reason, agent.messages[-1], ...)
        return
    
    # 2. 初始化周期状态
    invocation_state["event_loop_cycle_id"] = uuid.uuid4()
    invocation_state.setdefault("request_state", {})
    
    # 3. 检查点恢复
    resume_context = agent._checkpoint
    if resume_context is not None:
        agent._checkpoint = None
        next_cycle = resume_context.cycle_index + 1 if resume_context.position == "after_tools" \
                     else resume_context.cycle_index
        agent._checkpoint_cycle_index = next_cycle
        agent._checkpoint_resume_position = resume_context.position
    
    # 4. 启动追踪
    cycle_start_time, cycle_trace = agent.event_loop_metrics.start_cycle()
    yield StartEvent()
    yield StartEventLoopEvent()
    
    # 5. 周期追踪 span
    tracer = get_tracer()
    cycle_span = tracer.start_event_loop_cycle_span(...)
    invocation_state["event_loop_cycle_span"] = cycle_span
    
    with trace_api.use_span(cycle_span, end_on_exit=False):
        try:
            # 6. 检查中断状态或最新消息
            if agent._interrupt_state.activated and "tool_use_message" in agent._interrupt_state.context:
                stop_reason = "tool_use"
                message = agent._interrupt_state.context["tool_use_message"]
            elif _has_tool_use_in_latest_message(agent.messages):
                stop_reason = "tool_use"
                message = agent.messages[-1]
            else:
                # 7. 模型执行
                model_events = _handle_model_execution(agent, ...)
                async for model_event in model_events:
                    if not isinstance(model_event, ModelStopReason):
                        yield model_event
                stop_reason, message, *_ = model_event["stop"]
                yield ModelMessageEvent(message=message)
        
        except Exception as e:
            tracer.end_span_with_error(cycle_span, str(e), e)
            raise
        
        try:
            # 8. 处理 max_tokens
            if stop_reason == "max_tokens":
                raise MaxTokensReachedException(...)
            
            # 9. 工具执行
            if stop_reason == "tool_use":
                # 检查点保存
                if agent._checkpointing and not agent._cancel_signal.is_set() \
                   and not agent._interrupt_state.has_pending_tool_execution:
                    if agent._checkpoint_resume_position != "after_model":
                        yield _build_checkpoint_stop_event(agent, "after_model", ...)
                        return
                
                # 执行工具
                tool_events = _handle_tool_execution(
                    stop_reason, message, agent=agent, ...
                )
                async for tool_event in tool_events:
                    yield tool_event
                return
            
            # 10. 强制结构化输出
            if structured_output_context.is_enabled and stop_reason == "end_turn":
                if not structured_output_context.force_attempted:
                    structured_output_context.set_forced_mode()
                    await agent._append_messages({
                        "role": "user",
                        "content": [{"text": structured_output_context.structured_output_prompt}]
                    })
                    events = recurse_event_loop(agent=agent, ...)
                    async for typed_event in events:
                        yield typed_event
                    return
            
            # 11. 结束循环
            tracer.end_event_loop_cycle_span(cycle_span, message)
            yield EventLoopStopEvent(stop_reason, message, ...)
        
        except (StructuredOutputException, EventLoopException, 
                ContextWindowOverflowException, MaxTokensReachedException) as e:
            tracer.end_span_with_error(cycle_span, str(e), e)
            raise
        except Exception as e:
            tracer.end_span_with_error(cycle_span, str(e), e)
            yield ForceStopEvent(reason=e)
            raise EventLoopException(e, ...) from e
```

---

## 5. 模型执行

### 5.1 模型执行处理 `_handle_model_execution`

源码位置: `event_loop.py` 第 300-430 行

```python
async def _handle_model_execution(
    agent: "Agent",
    cycle_span: Any,
    cycle_trace: Trace,
    invocation_state: dict[str, Any],
    tracer: Tracer,
    structured_output_context: StructuredOutputContext,
) -> AsyncGenerator[TypedEvent, None]:
    """处理模型执行，包含重试逻辑"""
    
    stream_trace = Trace("stream_messages", parent_id=cycle_trace.id)
    cycle_trace.add_child(stream_trace)
    
    # 重试循环
    while True:
        try:
            # 1. 估算输入 token 数
            projected_input_tokens = await _estimate_input_tokens(agent)
            
            # 2. 触发 BeforeModelCallEvent 钩子
            before_model_call_event = BeforeModelCallEvent(
                agent=agent,
                invocation_state=invocation_state,
                projected_input_tokens=projected_input_tokens,
            )
            await agent.hooks.invoke_callbacks_async(before_model_call_event)
            
            # 3. 检查取消
            if before_model_call_event.cancel:
                message = {"role": "assistant", "content": [{"text": cancel_text}]}
                yield ModelStopReason(stop_reason="end_turn", message=message, ...)
                break
            
            # 4. 获取工具规格
            if structured_output_context.forced_mode:
                tool_spec = structured_output_context.get_tool_spec()
                tool_specs = [tool_spec] if tool_spec else []
            else:
                tool_specs = agent.tool_registry.get_all_tool_specs()
            
            # 5. 构建中间件上下文 (深度拷贝)
            middleware_context = InvokeModelContext(
                agent=agent,
                messages=copy.deepcopy(agent.messages),
                system_prompt=copy.deepcopy(agent._system_prompt_content),
                tool_specs=copy.deepcopy(tool_specs),
                tool_choice=copy.deepcopy(structured_output_context.tool_choice),
                invocation_state=invocation_state,
                model=agent.model,
                projected_input_tokens=projected_input_tokens,
            )
            
            # 6. 模型状态快照
            model_state_snapshot = copy.deepcopy(agent._model_state)
            
            # 7. 调用中间件链
            last_event = None
            async for event in agent._middleware_registry.invoke(
                InvokeModelStage,
                middleware_context,
                _make_invoke_model_terminal(agent, cycle_span, tracer, model_state_snapshot),
            ):
                last_event = event
                yield event
            
            # 8. 写回模型状态
            agent._model_state = model_state_snapshot
            
            # 9. 解析结果
            stop_reason, message, usage, metrics = last_event["stop"]
            
            # 10. 添加元数据到消息
            message["metadata"] = {"usage": usage, "metrics": metrics}
            
            # 11. 触发 AfterModelCallEvent 钩子
            after_model_call_event = AfterModelCallEvent(
                agent=agent,
                invocation_state=invocation_state,
                stop_response=AfterModelCallEvent.ModelStopResponse(
                    stop_reason=stop_reason, message=message
                ),
            )
            await agent.hooks.invoke_callbacks_async(after_model_call_event)
            
            # 12. 检查是否重试
            if after_model_call_event.retry:
                agent.event_loop_metrics.update_usage(usage)
                continue
            
            if stop_reason == "max_tokens":
                message = recover_message_on_max_tokens_reached(message)
            
            break  # 成功
        
        except Exception as e:
            after_model_call_event = AfterModelCallEvent(
                agent=agent, invocation_state=invocation_state, exception=e
            )
            await agent.hooks.invoke_callbacks_async(after_model_call_event)
            
            if after_model_call_event.retry:
                continue
            
            yield ForceStopEvent(reason=e)
            raise e
    
    # 添加消息到追踪
    stream_trace.add_message(message)
    stream_trace.end()
    
    # 添加响应消息到对话
    await agent._append_messages(message)
    
    # 更新指标
    agent.event_loop_metrics.update_usage(usage)
    agent.event_loop_metrics.update_metrics(metrics)
```

### 5.2 中间件终端 `_make_invoke_model_terminal`

```python
def _make_invoke_model_terminal(
    agent: "Agent", cycle_span: Any, tracer: Tracer, model_state: dict[str, Any]
) -> Callable[[InvokeModelContext], AsyncGenerator[Any, None]]:
    """创建 InvokeModelStage 中间件链的终端函数"""

    async def terminal(ctx: InvokeModelContext) -> AsyncGenerator[Any, None]:
        system_prompt_str, system_prompt_content = split_system_prompt(ctx.system_prompt)

        model_id = ctx.model.config.get("model_id") if hasattr(ctx.model, "config") else None
        model_invoke_span = tracer.start_model_invoke_span(
            messages=ctx.messages,
            parent_span=cycle_span,
            model_id=model_id,
            system_prompt=system_prompt_str,
            system_prompt_content=system_prompt_content,
        )

        with trace_api.use_span(model_invoke_span, end_on_exit=False):
            try:
                async for event in stream_messages(
                    ctx.model,
                    system_prompt_str,
                    ctx.messages,
                    ctx.tool_specs,
                    system_prompt_content=system_prompt_content,
                    tool_choice=ctx.tool_choice,
                    invocation_state=ctx.invocation_state,
                    model_state=model_state,
                    cancel_signal=agent._cancel_signal,
                ):
                    yield event

                stop_reason, message, usage, metrics = event["stop"]
                tracer.end_model_invoke_span(
                    model_invoke_span, message, usage, metrics, stop_reason
                )
            except Exception as e:
                tracer.end_span_with_error(model_invoke_span, str(e), e)
                raise

    return terminal
```

### 5.3 流处理 `stream_messages`

源码位置: `streaming.py` 第 220-320 行

```python
async def stream_messages(
    model: Model,
    system_prompt: str | None,
    messages: Messages,
    tool_specs: list[ToolSpec],
    *,
    tool_choice: Any | None = None,
    system_prompt_content: list[SystemContentBlock] | None = None,
    invocation_state: dict[str, Any] | None = None,
    model_state: dict[str, Any] | None = None,
    dynamic_trailing_blocks: int = 0,
    cancel_signal: threading.Event | None = None,
    **kwargs: Any,
) -> AsyncGenerator[TypedEvent, None]:
    """流式消息处理"""
    
    # 1. 规范化消息
    messages = _normalize_messages(messages)
    
    # 2. 白名单过滤
    messages = [Message(role=msg["role"], content=msg["content"]) for msg in messages]
    
    start_time = time.time()
    
    # 3. 调用模型流
    chunks = model.stream(
        messages,
        tool_specs if tool_specs else None,
        system_prompt,
        tool_choice=tool_choice,
        system_prompt_content=system_prompt_content,
        invocation_state=invocation_state,
        model_state=model_state,
        dynamic_trailing_blocks=dynamic_trailing_blocks,
    )
    
    # 4. 处理流
    async for event in process_stream(chunks, start_time, cancel_signal):
        yield event
```

### 5.4 流处理器 `process_stream`

```python
async def process_stream(
    chunks: AsyncIterable[StreamEvent],
    start_time: float | None = None,
    cancel_signal: threading.Event | None = None,
) -> AsyncGenerator[TypedEvent, None]:
    """处理模型响应流"""

    state = {
        "message": {"role": "assistant", "content": []},
        "text": "",
        "current_tool_use": {},
        "reasoningText": "",
        "citationsContent": [],
    }
    state["content"] = state["message"]["content"]

    first_byte_time = None

    async for chunk in chunks:
        # 检查取消
        if cancel_signal and cancel_signal.is_set():
            yield ModelStopReason(
                stop_reason="cancelled",
                message={"role": "assistant", "content": [{"text": "Cancelled by user"}]},
                usage=usage,
                metrics=metrics,
            )
            return

        # 追踪首字节时间
        if first_byte_time is None and (
            "contentBlockDelta" in chunk or "contentBlockStart" in chunk
        ):
            first_byte_time = time.time()

        yield ModelStreamChunkEvent(chunk=chunk)

        # 处理不同类型的 chunk
        if "messageStart" in chunk:
            state["message"] = handle_message_start(chunk["messageStart"], state["message"])

        elif "contentBlockStart" in chunk:
            state["current_tool_use"] = handle_content_block_start(chunk["contentBlockStart"])

        elif "contentBlockDelta" in chunk:
            state, typed_event = handle_content_block_delta(chunk["contentBlockDelta"], state)
            yield typed_event

        elif "contentBlockStop" in chunk:
            state = handle_content_block_stop(state)

        elif "messageStop" in chunk:
            stop_reason = handle_message_stop(
                chunk["messageStop"], state["message"].get("content", [])
            )

        elif "metadata" in chunk:
            time_to_first_byte_ms = (
                int(1000 * (first_byte_time - start_time))
                if first_byte_time and start_time
                else None
            )
            usage, metrics = extract_usage_metrics(chunk["metadata"], time_to_first_byte_ms)

        elif "redactContent" in chunk:
            handle_redact_content(chunk["redactContent"], state)

    yield ModelStopReason(
        stop_reason=stop_reason, message=state["message"], usage=usage, metrics=metrics
    )
```

---

## 6. 工具执行

### 6.1 工具执行处理 `_handle_tool_execution`

源码位置: `event_loop.py` 第 495-640 行

```python
async def _handle_tool_execution(
    stop_reason: StopReason,
    message: Message,
    agent: "Agent",
    cycle_trace: Trace,
    cycle_span: Any,
    cycle_start_time: float,
    invocation_state: dict[str, Any],
    tracer: Tracer,
    structured_output_context: StructuredOutputContext,
    limits: Limits | None = None,
) -> AsyncGenerator[TypedEvent, None]:
    """处理模型请求的工具执行"""
    
    # 1. 提取工具调用
    tool_uses: list[ToolUse] = [
        content["toolUse"] for content in message["content"] if "toolUse" in content
    ]
    tool_results: list[ToolResult] = []
    
    # 2. 合并中断恢复的工具结果
    if agent._interrupt_state.activated and "tool_results" in agent._interrupt_state.context:
        tool_results.extend(agent._interrupt_state.context["tool_results"])
        tool_use_ids = {tr["toolUseId"] for tr in tool_results}
        tool_uses = [tu for tu in tool_uses if tu["toolUseId"] not in tool_use_ids]
    
    # 3. 触发 BeforeToolsEvent 钩子
    before_tools_event = BeforeToolsEvent(
        agent=agent,
        message=message,
        invocation_state=invocation_state,
    )
    before_tools_event, interrupts = await agent.hooks.invoke_callbacks_async(before_tools_event)
    
    if interrupts:
        async for interrupt_event in _stop_for_interrupts(...):
            yield interrupt_event
        return
    
    # 4. 检查取消
    cancel_message = None
    if before_tools_event.cancel:
        cancel_message = "Tool cancelled by hook"
    elif agent._cancel_signal.is_set():
        cancel_message = "Tool execution cancelled"
    
    # 5. 执行工具
    try:
        if cancel_message:
            # 取消的工具结果
            for tool_use in tool_uses:
                cancel_result = {
                    "toolUseId": tool_use["toolUseId"],
                    "status": "error",
                    "content": [{"text": cancel_message}],
                }
                tool_results.append(cancel_result)
                yield ToolResultEvent(cancel_result)
        else:
            # 验证工具
            validated_tool_uses, validation_results, invalid_ids = [], [], []
            validate_and_prepare_tools(message, validated_tool_uses, validation_results, invalid_ids)
            
            tool_uses = [tu for tu in validated_tool_uses 
                        if tu["toolUseId"] in pending_tool_use_ids 
                        and tu["toolUseId"] not in invalid_ids]
            tool_results.extend(result for result in validation_results ...)
            
            # 执行工具
            tool_events = agent.tool_executor._execute(
                agent, tool_uses, tool_results, cycle_trace, ...
            )
            async for tool_event in tool_events:
                if isinstance(tool_event, ToolInterruptEvent):
                    interrupts.extend(tool_event["tool_interrupt_event"]["interrupts"])
                yield tool_event
    
    finally:
        # 6. 触发 AfterToolsEvent 钩子
        tool_result_message = {
            "role": "user",
            "content": [{"toolResult": result} for result in tool_results],
        }
        after_tools_event = AfterToolsEvent(
            agent=agent,
            message=tool_result_message,
            invocation_state=invocation_state,
        )
        try:
            after_tools_event, _ = await agent.hooks.invoke_callbacks_async(after_tools_event)
        except Exception:
            if interrupts:
                agent._interrupt_state.context = {"tool_use_message": message, "tool_results": tool_results}
                agent._interrupt_state.activate()
            raise
    
    # 7. 中断处理
    if interrupts:
        async for interrupt_event in _stop_for_interrupts(...):
            yield interrupt_event
        return
    
    # 8. 重置中断状态
    if not agent._cancel_signal.is_set():
        agent._interrupt_state.end_tool_cycle()
    
    # 9. 添加工具结果消息
    await agent._append_messages(tool_result_message)
    yield ToolResultMessageEvent(message=tool_result_message)
    
    # 10. 检查提前结束
    if after_tools_event.end_turn:
        end_turn_message = {"role": "assistant", "content": [{"text": end_turn_text}]}
        await agent._append_messages(end_turn_message)
        yield EventLoopStopEvent("end_turn", end_turn_message, ...)
        return
    
    if invocation_state["request_state"].get("stop_event_loop", False) or structured_output_context.stop_loop:
        yield EventLoopStopEvent(stop_reason, message, ...)
        return
    
    if agent._cancel_signal.is_set():
        yield EventLoopStopEvent("cancelled", message, ...)
        return
    
    # 11. 检查点保存
    if agent._checkpointing:
        cycle_index = agent._checkpoint_cycle_index
        agent._checkpoint_cycle_index = cycle_index + 1
        yield _build_checkpoint_stop_event(agent, "after_tools", cycle_index, ...)
        return
    
    # 12. 递归调用事件循环
    events = recurse_event_loop(agent=agent, invocation_state=invocation_state, ...)
    async for event in events:
        yield event
```

### 6.2 并发工具执行器 `ConcurrentToolExecutor`

源码位置: `executors/concurrent.py`

```python
class ConcurrentToolExecutor(ToolExecutor):
    """并发工具执行器"""

    async def _execute(
        self,
        agent: "Agent",
        tool_uses: list[ToolUse],
        tool_results: list[ToolResult],
        cycle_trace: Trace,
        cycle_span: Any,
        invocation_state: dict[str, Any],
        structured_output_context: "StructuredOutputContext | None" = None,
    ) -> AsyncGenerator[TypedEvent, None]:
        """并发执行所有工具"""

        task_queue: asyncio.Queue = asyncio.Queue()
        task_events = [asyncio.Event() for _ in tool_uses]
        task_results: list[list[ToolResult]] = [[] for _ in tool_uses]
        stop_event = object()

        # 创建所有任务
        tasks = []
        for task_id, tool_use in enumerate(tool_uses):
            task = asyncio.create_task(
                self._task(
                    agent,
                    tool_use,
                    task_results[task_id],
                    cycle_trace,
                    cycle_span,
                    invocation_state,
                    task_id,
                    task_queue,
                    task_events[task_id],
                    stop_event,
                    structured_output_context,
                )
            )
            tasks.append(task)

        # 收集结果
        task_count = len(tasks)
        try:
            while task_count:
                task_id, event = await task_queue.get()
                if event is stop_event:
                    task_count -= 1
                    continue

                if isinstance(event, Exception):
                    raise event

                yield event
                task_events[task_id].set()

            for results in task_results:
                tool_results.extend(results)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _task(
        self,
        agent,
        tool_use,
        tool_results,
        cycle_trace,
        cycle_span,
        invocation_state,
        task_id,
        task_queue,
        task_event,
        stop_event,
        structured_output_context,
    ) -> None:
        """执行单个工具任务"""
        try:
            events = ToolExecutor._stream_with_trace(
                agent,
                tool_use,
                tool_results,
                cycle_trace,
                cycle_span,
                invocation_state,
                structured_output_context,
            )
            async for event in events:
                task_queue.put_nowait((task_id, event))
                await task_event.wait()
                task_event.clear()
        except Exception as e:
            task_queue.put_nowait((task_id, e))
        finally:
            task_queue.put_nowait((task_id, stop_event))
```

### 6.3 工具执行流 `ToolExecutor._stream`

源码位置: `executors/_executor.py` 第 50-160 行

```python
@staticmethod
async def _stream(
    agent: "Agent | BidiAgent",
    tool_use: ToolUse,
    tool_results: list[ToolResult],
    invocation_state: dict[str, Any],
    structured_output_context: StructuredOutputContext | None = None,
    **kwargs: Any,
) -> AsyncGenerator[TypedEvent, None]:
    """流式工具执行，包含钩子和中间件"""

    tool_name = tool_use["name"]

    # 1. 查找工具
    tool_info = agent.tool_registry.dynamic_tools.get(tool_name)
    tool_func = tool_info if tool_info else agent.tool_registry.registry.get(tool_name)
    tool_spec = tool_func.tool_spec if tool_func else None

    # 2. 设置追踪属性
    current_span = trace_api.get_current_span()
    if current_span and tool_spec:
        current_span.set_attribute("gen_ai.tool.description", tool_spec["description"])
        current_span.set_attribute("gen_ai.tool.json_schema", serialize(tool_spec["inputSchema"]))

    # 3. 更新调用状态
    invocation_state.update(
        {
            "agent": agent,
            "model": agent.model,
            "messages": agent.messages,
            "system_prompt": agent.system_prompt,
            "tool_config": ToolConfig(...),
        }
    )

    # 重试循环
    while True:
        # 4. BeforeToolCallEvent 钩子
        before_event, interrupts = await ToolExecutor._invoke_before_tool_call_hook(
            agent, tool_func, tool_use, invocation_state
        )

        if interrupts:
            yield ToolInterruptEvent(tool_use, interrupts)
            return

        if before_event.cancel_tool:
            cancel_result = {
                "toolUseId": str(tool_use.get("toolUseId")),
                "status": "error",
                "content": [{"text": cancel_message}],
            }
            yield ToolResultEvent(cancel_result)
            tool_results.append(cancel_result)
            return

        try:
            tool_start_time = time.monotonic()
            selected_tool = before_event.selected_tool
            tool_use = before_event.tool_use

            # 5. 构建中间件上下文
            middleware_context = ExecuteToolContext(
                agent=agent,
                tool=selected_tool,
                tool_use=dict(tool_use),
                invocation_state=invocation_state,
                _interrupt_state=agent._interrupt_state,
            )

            # 6. 调用中间件链
            result_event = None
            async for event in agent._middleware_registry.invoke(
                ExecuteToolStage,
                middleware_context,
                _make_execute_tool_terminal(kwargs),
            ):
                if isinstance(event, ToolInterruptEvent):
                    for interrupt in event.interrupts:
                        agent._interrupt_state.interrupts.setdefault(interrupt.id, interrupt)
                    yield event
                    return

                if isinstance(event, ToolResultEvent):
                    result_event = event
                else:
                    yield event

            result = result_event.tool_result

            # 7. 触发 AfterToolCallEvent 钩子
            tool_duration = time.monotonic() - tool_start_time
            after_event = await ToolExecutor._invoke_after_tool_call_hook(
                agent, selected_tool, tool_use, invocation_state, result, duration=tool_duration
            )

            if ToolExecutor._should_retry(agent, after_event):
                continue

            yield ToolResultEvent(after_event.result, exception=after_event.exception)
            tool_results.append(after_event.result)
            return

        except InterruptException as interrupt_exception:
            agent._interrupt_state.interrupts.setdefault(
                interrupt_exception.interrupt.id, interrupt_exception.interrupt
            )
            yield ToolInterruptEvent(tool_use, [interrupt_exception.interrupt])
            return

        except Exception as e:
            error_result = {
                "toolUseId": str(tool_use.get("toolUseId")),
                "status": "error",
                "content": [{"text": f"Error: {str(e)}"}],
            }
            after_event = await ToolExecutor._invoke_after_tool_call_hook(
                agent, selected_tool, tool_use, invocation_state, error_result, exception=e
            )

            if ToolExecutor._should_retry(agent, after_event):
                continue

            yield ToolResultEvent(after_event.result, exception=after_event.exception)
            tool_results.append(after_event.result)
            return
```

---

## 7. 工具注册系统

源码位置: `tools/registry.py`

### 7.1 工具注册表类

```python
class ToolRegistry:
    """工具注册表核心类"""
    
    def __init__(self) -> None:
        self.registry: dict[str, AgentTool] = {}
        self.dynamic_tools: dict[str, AgentTool] = {}
        self.tool_config: dict[str, Any] | None = None
        self._tool_providers: list[ToolProvider] = []
        self._registry_id = str(uuid.uuid4())
```

### 7.2 处理工具列表

```python
def process_tools(self, tools: list[Any]) -> list[str]:
    """处理多种格式的工具输入"""
    
    tool_names = []
    
    def add_tool(tool: Any) -> None:
        # 1. 字符串格式
        #  - 本地文件路径: "./path/to/tool.py"
        #  - 模块路径: "strands_tools.file_read"
        #  - 模块路径+函数: "module:func_name"
        if isinstance(tool, str):
            tools = load_tool_from_string(tool)
            for a_tool in tools:
                a_tool.mark_dynamic()
                self.register_tool(a_tool)
                tool_names.append(a_tool.tool_name)
        
        # 2. 字典格式 {"name": "x", "path": "/path"}
        elif isinstance(tool, dict) and "name" in tool and "path" in tool:
            tools = load_tool_from_string(tool["path"])
            for a_tool in tools:
                if a_tool.tool_name == tool["name"]:
                    self.register_tool(a_tool)
                    tool_names.append(a_tool.tool_name)
        
        # 3. Python 模块
        elif hasattr(tool, "__file__") and inspect.ismodule(tool):
            tools = load_tools_from_module(tool, module_name)
            for a_tool in tools:
                self.register_tool(a_tool)
                tool_names.append(a_tool.tool_name)
        
        # 4. AgentTool 实例 (@tool 装饰器)
        elif isinstance(tool, AgentTool):
            self.register_tool(tool)
            tool_names.append(tool.tool_name)
        
        # 5. 嵌套列表
        elif isinstance(tool, Iterable) and not isinstance(tool, (str, bytes, bytearray)):
            for t in tool:
                add_tool(t)
        
        # 6. ToolProvider
        elif isinstance(tool, ToolProvider):
            self._tool_providers.append(tool)
            provider_tools = await tool.load_tools()
            for provider_tool in provider_tools:
                self.register_tool(provider_tool)
                tool_names.append(provider_tool.tool_name)
        
        # 7. Agent 实例
        elif isinstance(tool, AgentBase) and hasattr(tool, "as_tool"):
            wrapped_tool = tool.as_tool()
            self.register_tool(wrapped_tool)
            tool_names.append(wrapped_tool.tool_name)
    
    for tool in tools:
        add_tool(tool)
    
    return tool_names
```

### 7.3 注册工具

```python
def register_tool(self, tool: AgentTool) -> None:
    """注册单个工具"""

    # 检查重复
    if tool.tool_name in self.registry and not tool.supports_hot_reload:
        raise ValueError(f"Tool name '{tool.tool_name}' already exists")

    # 检查名称冲突 (- vs _)
    normalized_name = tool.tool_name.replace("-", "_")
    matching_tools = [
        tn for (tn, t) in self.registry.items() if tn.replace("-", "_") == normalized_name
    ]
    if matching_tools:
        raise ValueError(f"Tool name '{tool.tool_name}' already exists as '{matching_tools[0]}'")

    # 注册到主表
    self.registry[tool.tool_name] = tool

    # 注册到动态工具表
    if tool.is_dynamic:
        self.dynamic_tools[tool.tool_name] = tool
```

### 7.4 获取工具规格

```python
def get_all_tool_specs(self) -> list[ToolSpec]:
    """获取所有工具规格列表"""
    all_tools = self.get_all_tools_config()
    return list(all_tools.values())


def get_all_tools_config(self) -> dict[str, Any]:
    """获取所有工具配置"""
    tool_config = {}

    for tool_name, tool in self.registry.items():
        spec = tool.tool_spec.copy()
        try:
            spec = normalize_tool_spec(spec)
            self.validate_tool_spec(spec)
            tool_config[tool_name] = spec
        except (ValueError, RecursionError) as e:
            logger.warning("tool spec validation failed: %s", e)

    for tool_name, tool in self.dynamic_tools.items():
        if tool_name not in tool_config:
            spec = tool.tool_spec.copy()
            tool_config[tool_name] = spec

    return tool_config
```

---

## 8. 对话管理器系统

### 8.1 基类 `ConversationManager`

源码位置: `conversation_manager/conversation_manager.py`

```python
class ConversationManager(ABC, HookProvider):
    """对话管理器抽象基类"""
    
    def __init__(self, proactive_compression: Union[bool, ProactiveCompressionConfig, None] = None):
        # 解析压缩阈值
        if proactive_compression is True:
            threshold = 0.7
        elif isinstance(proactive_compression, dict):
            threshold = proactive_compression.get("compression_threshold", 0.7)
        else:
            threshold = None
        
        self._compression_threshold = threshold
    
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        """注册钩子 - 必须实现"""
        registry.add_callback(BeforeModelCallEvent, self._on_before_model_call_threshold)
    
    @abstractmethod
    def apply_management(self, agent: "Agent", **kwargs) -> None:
        """应用管理策略"""
        pass
    
    @abstractmethod
    def reduce_context(self, agent: "Agent", e: Exception | None = None, **kwargs) -> None:
        """减少上下文"""
        pass
```

### 8.2 滑动窗口管理器 `SlidingWindowConversationManager`

源码位置: `conversation_manager/sliding_window_conversation_manager.py`

```python
class SlidingWindowConversationManager(ConversationManager):
    """滑动窗口对话管理器"""
    
    def __init__(
        self,
        window_size: int = 40,
        should_truncate_results: bool = True,
        per_turn: bool | int = False,
        pin_first: int | None = None,
        proactive_compression: bool | ProactiveCompressionConfig | None = None,
    ):
        super().__init__(proactive_compression=proactive_compression)
        self.window_size = window_size
        self.should_truncate_results = should_truncate_results
        self.per_turn = per_turn
        self.pin_first = pin_first
    
    def apply_management(self, agent: "Agent", **kwargs) -> None:
        """应用滑动窗口"""
        messages = agent.messages
        
        if len(messages) <= self.window_size:
            return
        
        self.reduce_context(agent)
    
    def reduce_context(self, agent: "Agent", e: Exception | None = None, **kwargs) -> None:
        """减少上下文 - 截断或修剪"""
        messages = agent.messages
        
        # 1. 固定前 N 条消息
        if self.pin_first and not self._pin_first_applied:
            apply_pin_first(messages, self.pin_first)
            self._pin_first_applied = True
        
        # 2. window_size=0 清除所有
        if self.window_size == 0:
            pinned = [messages[i] for i in range(len(messages)) if is_pinned(messages, i)]
            self.removed_message_count += len(messages) - len(pinned)
            messages[:] = pinned
            return
        
        # 3. 尝试截断工具结果 (仅用于溢出恢复)
        if e is not None:
            oldest_idx = self._find_oldest_message_with_tool_results(messages)
            if oldest_idx and self.should_truncate_results:
                if self._truncate_tool_results(messages, oldest_idx):
                    return
        
        # 4. 找到有效修剪点
        start_index = 2 if len(messages) <= self.window_size else len(messages) - self.window_size
        trim_index = find_valid_trim_point(messages, start_index)
        
        if trim_index >= len(messages):
            # 备用: 找到 toolUse/toolResult 对边界
            fallback = self._find_tool_pair_trim_point(messages, start_index)
            if fallback:
                trim_index = fallback
            elif e:
                raise ContextWindowOverflowException(...)
        
        # 5. 删除非固定消息
        indices_to_remove = [i for i in range(trim_index) if not is_pinned(messages, i)]
        
        if not indices_to_remove:
            if e:
                raise ContextWindowOverflowException(...)
            return
        
        self.removed_message_count += len(indices_to_remove)
        
        # 逆序删除保持索引稳定
        for i in reversed(indices_to_remove):
            del messages[i]
```

### 8.3 总结管理器 `SummarizingConversationManager`

源码位置: `conversation_manager/summarizing_conversation_manager.py`

```python
class SummarizingConversationManager(ConversationManager):
    """总结式对话管理器"""

    def __init__(
        self,
        summary_ratio: float = 0.3,
        preserve_recent_messages: int = 10,
        summarization_agent: Optional["Agent"] = None,
        summarization_system_prompt: str | None = None,
        pin_first: int | None = None,
        proactive_compression: bool | ProactiveCompressionConfig | None = None,
    ):
        super().__init__(proactive_compression=proactive_compression)
        self.summary_ratio = max(0.1, min(0.8, summary_ratio))
        self.preserve_recent_messages = preserve_recent_messages
        self.summarization_agent = summarization_agent
        self.summarization_system_prompt = summarization_system_prompt
        self.pin_first = pin_first
        self._summary_message: Message | None = None

    def reduce_context(self, agent: "Agent", e: Exception | None = None, **kwargs) -> None:
        """使用总结减少上下文"""
        try:
            self._summarize_oldest(agent)
        except Exception as summarization_error:
            if e is not None:
                raise summarization_error from e
            logger.warning("Proactive summarization failed: %s", summarization_error)

    def _summarize_oldest(self, agent: "Agent") -> None:
        """总结最旧的消息"""
        # 1. 计算要总结的消息数
        messages_to_summarize_count = max(1, int(len(agent.messages) * self.summary_ratio))
        messages_to_summarize_count = min(
            messages_to_summarize_count, len(agent.messages) - self.preserve_recent_messages
        )

        # 2. 调整分割点避免破坏工具对
        messages_to_summarize_count = self._adjust_split_point_for_tool_pairs(
            agent.messages, messages_to_summarize_count
        )

        # 3. 固定前 N 条消息
        if self.pin_first and not self._pin_first_applied:
            apply_pin_first(agent.messages, self.pin_first)
            self._pin_first_applied = True

        # 4. 分区固定和非固定消息
        protected_to_preserve, to_summarize = partition_pinned(
            agent.messages, 0, messages_to_summarize_count
        )

        remaining_messages = agent.messages[messages_to_summarize_count:]

        # 5. 生成总结
        self._summary_message = self._generate_summary(to_summarize, agent)
        _ensure_tracking_id(self._summary_message)

        # 6. 替换为总结
        agent.messages[:] = protected_to_preserve + [self._summary_message] + remaining_messages

    def _generate_summary(self, messages: list[Message], agent: "Agent") -> Message:
        """生成消息总结"""
        if self.summarization_agent:
            return self._generate_summary_with_agent(messages)
        return self._generate_summary_with_model(messages, agent)
```

---

## 9. 钩子系统

源码位置: `hooks/registry.py`

### 9.1 钩子注册表类

```python
class HookRegistry:
    """钩子注册表"""
    
    def __init__(self) -> None:
        self._registered_callbacks: dict[type, list[_CallbackEntry]] = {}
    
    def add_callback(
        self,
        event_type: type[TEvent] | list[type[TEvent]] | None,
        callback: HookCallback[TEvent],
        *,
        order: float = HookOrder.DEFAULT,
    ) -> None:
        """注册回调"""
        # 解析事件类型
        if isinstance(event_type, list):
            resolved_event_types = self._validate_event_type_list(event_type)
        elif event_type is None:
            resolved_event_types = infer_event_types(callback)
        else:
            resolved_event_types = [event_type]
        
        # 注册回调
        for resolved_event_type in unique_event_types:
            entries = self._registered_callbacks.setdefault(resolved_event_type, [])
            entry = _CallbackEntry(callback=callback, order=order)
            bisect.insort(entries, entry, key=lambda e: e.order)
```

### 9.2 调用回调

```python
async def invoke_callbacks_async(self, event: TInvokeEvent) -> tuple[TInvokeEvent, list[Interrupt]]:
    """异步调用所有注册的回调"""
    interrupts: dict[str, Interrupt] = {}

    for callback in self.get_callbacks_for(event):
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except InterruptException as exception:
            interrupt = exception.interrupt
            interrupts[interrupt.name] = interrupt

    return event, list(interrupts.values())


def get_callbacks_for(self, event: TEvent) -> Generator[HookCallback[TEvent], None, None]:
    """获取事件类型的回调"""
    event_type = type(event)
    entries = self._registered_callbacks.get(event_type, [])

    if event.should_reverse_callbacks:
        for _order, group in groupby(entries, key=lambda e: e.order):
            for entry in reversed(list(group)):
                yield entry.callback
    else:
        for entry in entries:
            yield entry.callback
```

### 9.3 可用事件类型

| 事件类型 | 触发时机 | 用途 |
|---------|---------|------|
| `AgentInitializedEvent` | Agent 初始化完成 | 初始化检查、资源分配 |
| `BeforeInvocationEvent` | 调用开始前 | 输入验证、权限检查 |
| `AfterInvocationEvent` | 调用完成后 | 结果后处理、清理 |
| `BeforeModelCallEvent` | 模型调用前 | 输入修改、缓存检查 |
| `AfterModelCallEvent` | 模型调用后 | 结果修改、日志记录 |
| `BeforeToolsEvent` | 工具执行前 | 授权检查、安全过滤 |
| `AfterToolsEvent` | 工具执行后 | 结果后处理 |
| `BeforeToolCallEvent` | 单个工具调用前 | 输入验证 |
| `AfterToolCallEvent` | 单个工具调用后 | 结果处理 |
| `MessageAddedEvent` | 消息添加时 | 消息记录 |

### 9.4 钩子优先级

```python
class HookOrder:
    """钩子执行优先级"""

    SDK_FIRST: int = -100  # 最先执行
    INTERVENTION_OUTPUT: int = -90
    DEFAULT: int = 0  # 默认优先级
    MODEL_ROUTING: int = 50
    INTERVENTION_INPUT: int = 90
    SDK_LAST: int = 100  # 最后执行
```

---

## 10. 中间件系统

### 10.1 中间件注册表

源码位置: `_middleware/registry.py`

```python
class MiddlewareRegistry:
    """中间件注册表"""
    
    def __init__(self) -> None:
        self._handlers: dict[MiddlewareStage, list[_TaggedHandler]] = {}
    
    def add_middleware(
        self,
        stage_or_phase,
        handler: Any,
    ) -> None:
        """注册中间件"""
        if isinstance(stage_or_phase, MiddlewareInputPhase):
            self._add_input(stage_or_phase, handler)
        elif isinstance(stage_or_phase, MiddlewareOutputPhase):
            self._add_output(stage_or_phase, handler)
        elif isinstance(stage_or_phase, MiddlewareWrapPhase):
            self._add_wrap(stage_or_phase._stage, handler)
        else:
            self._add_wrap(stage_or_phase, handler)
```

### 10.2 中间件组成

```python
def compose(self, stage: MiddlewareStage, terminal: MiddlewareNext) -> MiddlewareNext:
    """组合中间件链"""
    tagged = self._handlers.get(stage)
    if not tagged:
        return terminal
    
    # 按阶段排序: input(0) → output(1) → wrap(2)
    sorted_handlers = sorted(tagged, key=lambda t: _PHASE_ORDER[t.phase])
    
    current = terminal
    for handler in reversed(sorted_handlers):
        next_fn = current
        
        def _make_layer(h, nf):
            async def layer(ctx):
                inner_gens = []
                
                def tracking_next(c):
                    gen = nf(c)
                    inner_gens.append(gen)
                    return gen
                
                handler_gen = h(ctx, tracking_next)
                try:
                    async for event in handler_gen:
                        yield event
                finally:
                    await handler_gen.aclose()
                    for gen in inner_gens:
                        await gen.aclose()
            
            return layer
        
        current = _make_layer(handler.handler, next_fn)
    
    return current
```

### 10.3 内置阶段

```python
# 模型调用阶段
InvokeModelStage: MiddlewareStage[InvokeModelContext, ModelStopReason, TypedEvent]

# 工具执行阶段
ExecuteToolStage: MiddlewareStage[ExecuteToolContext, ToolResultEvent, TypedEvent]

# 代理流阶段 (最外层)
AgentStreamStage: MiddlewareStage[AgentStreamContext, EventLoopStopEvent, TypedEvent]
```

### 10.4 上下文类

```python
@dataclass
class InvokeModelContext:
    """InvokeModelStage 中间件上下文"""

    agent: Agent
    messages: Messages  # 深度拷贝
    system_prompt: SystemPrompt  # 深度拷贝
    tool_specs: list[ToolSpec]  # 深度拷贝
    tool_choice: ToolChoice | None  # 深度拷贝
    invocation_state: dict[str, Any]  # 引用共享
    model: Model
    projected_input_tokens: int | None = None


@dataclass
class ExecuteToolContext:
    """ExecuteToolStage 中间件上下文"""

    agent: Agent | BidiAgent
    tool: AgentTool | None
    tool_use: ToolUse  # 浅拷贝
    invocation_state: dict[str, Any]  # 引用共享
    _interrupt_state: _InterruptState

    def interrupt(self, name: str, *, reason=None, response=None) -> MiddlewareInterruptResult:
        """请求人工干预中断"""
        ...


@dataclass
class AgentStreamContext:
    """AgentStreamStage 中间件上下文"""

    agent: Agent
    messages: Messages  # 引用共享
    invocation_state: dict[str, Any]  # 引用共享
    _interrupts: Mapping[str, Interrupt]  # 中断快照

    def interrupt(self, name: str, *, reason=None, response=None) -> MiddlewareInterruptResult:
        """请求人工干预中断"""
        ...
```

---

## 11. 重试策略

源码位置: `event_loop/_retry.py`

```python
class ModelRetryStrategy(HookProvider):
    """指数退避重试策略"""

    def __init__(
        self,
        *,
        max_attempts: int = 6,
        initial_delay: int = 4,
        max_delay: int = 240,
    ):
        self._max_attempts = max_attempts
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._current_attempt = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterModelCallEvent, self._handle_after_model_call)
        registry.add_callback(AfterInvocationEvent, self._handle_after_invocation)

    def is_retryable(self, exception: Exception) -> bool:
        """判断异常是否可重试"""
        return isinstance(exception, ModelThrottledException)

    def _calculate_delay(self, attempt: int) -> int:
        """计算延迟 (指数退避)"""
        delay = self._initial_delay * (2**attempt)
        return min(delay, self._max_delay)

    async def _handle_after_model_call(self, event: AfterModelCallEvent) -> None:
        """处理模型调用结果"""
        # 1. 检查是否成功
        if event.stop_response is not None:
            self._reset_retry_state()
            return

        if event.exception is None:
            self._reset_retry_state()
            return

        # 2. 检查是否可重试
        if not self.is_retryable(event.exception):
            return

        # 3. 增加尝试次数
        self._current_attempt += 1

        # 4. 检查是否超过最大次数
        if self._current_attempt >= self._max_attempts:
            return

        # 5. 计算延迟
        delay = self._calculate_delay(self._current_attempt)

        # 6. 等待
        await asyncio.sleep(delay)

        # 7. 设置重试标志
        event.retry = True
```

### 延迟时间表

| 尝试次数 | 延迟 |
|---------|------|
| 1 | 4s |
| 2 | 8s |
| 3 | 16s |
| 4 | 32s |
| 5 | 64s |
| 6 | 停止重试 |

---

## 12. 直接工具调用

源码位置: `tools/_caller.py`

### 12.1 工具调用器类

```python
class _ToolCaller:
    """直接工具调用器"""

    def __init__(self, agent: "Agent | BidiAgent") -> None:
        self._agent_ref = weakref.ref(agent)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """动态属性访问: agent.tool.my_tool()"""

        def caller(
            user_message_override: str | None = None,
            record_direct_tool_call: bool | None = None,
            **kwargs: Any,
        ) -> Any:
            if self._agent._interrupt_state.activated:
                raise RuntimeError("cannot directly call tool during interrupt")

            should_record_direct_tool_call = (
                record_direct_tool_call
                if record_direct_tool_call is not None
                else self._agent.record_direct_tool_call
            )

            # 获取锁
            should_lock = should_record_direct_tool_call
            acquired_lock = (
                should_lock
                and isinstance(self._agent, Agent)
                and self._agent._concurrency.try_acquire_lock()
            )

            try:
                # 规范化工具名
                normalized_name = self._find_normalized_tool_name(name)

                # 创建工具请求
                tool_id = f"tooluse_{name}_{random.randint(100000000, 999999999)}"
                tool_use: ToolUse = {
                    "toolUseId": tool_id,
                    "name": normalized_name,
                    "input": kwargs.copy(),
                }
                tool_results: list[ToolResult] = []

                # 执行工具
                async def acall() -> ToolResult:
                    async for event in ToolExecutor._stream(
                        self._agent, tool_use, tool_results, kwargs
                    ):
                        if isinstance(event, ToolInterruptEvent):
                            self._agent._interrupt_state.deactivate()
                            raise RuntimeError("cannot raise interrupt in direct tool call")

                    return tool_results[0]

                tool_result = run_async(acall)

                # 记录到消息历史
                if should_record_direct_tool_call:
                    await self._record_tool_execution(tool_use, tool_result, user_message_override)

                # 应用对话管理
                if isinstance(self._agent, Agent):
                    self._agent.conversation_manager.apply_management(self._agent)

                return tool_result

            finally:
                if acquired_lock and isinstance(self._agent, Agent):
                    self._agent._concurrency.release_lock()

        return caller

    def _find_normalized_tool_name(self, name: str) -> str:
        """查找工具名 (支持 - 和 _ 互换)"""
        tool_registry = self._agent.tool_registry.registry

        if tool_registry.get(name):
            return name

        if "_" in name:
            filtered_tools = [
                tn for (tn, t) in tool_registry.items() if tn.replace("-", "_") == name
            ]
            if filtered_tools:
                return filtered_tools[0]

        raise AttributeError(f"Tool '{name}' not found")
```

### 12.2 记录工具执行

```python
async def _record_tool_execution(
    self,
    tool: ToolUse,
    tool_result: ToolResult,
    user_message_override: str | None,
) -> None:
    """记录工具执行到消息历史"""

    # 创建用户消息
    user_msg_content = [
        {
            "text": f"agent.tool.{tool['name']} direct tool call.\n"
            f"Input parameters: {json.dumps(tool['input'])}\n"
        }
    ]

    if user_message_override:
        user_msg_content.insert(0, {"text": f"{user_message_override}\n"})

    # 创建消息序列
    user_msg = {"role": "user", "content": user_msg_content}
    tool_use_msg = {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tool["toolUseId"],
                    "name": tool["name"],
                    "input": self._filter_tool_parameters_for_recording(
                        tool["name"], tool["input"]
                    ),
                }
            }
        ],
    }
    tool_result_msg = {"role": "user", "content": [{"toolResult": tool_result}]}
    assistant_msg = {
        "role": "assistant",
        "content": [{"text": f"agent.tool.{tool['name']} was called."}],
    }

    # 添加到消息历史
    await self._agent._append_messages(user_msg, tool_use_msg, tool_result_msg, assistant_msg)
```

---

## 13. 完整执行流程图

```
┌──────────────────────────────────────────────────────────────────────┐
│                     agent("用户输入")                                 │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      stream_async()                                  │
│  1. 并发控制 (_ConcurrencyController)                               │
│  2. 初始化指标和追踪                                                 │
│  3. 消息转换 (_convert_prompt_to_messages)                          │
│  4. 启动追踪 span                                                   │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       _run_loop()                                     │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    事件循环 (可递归)                           │  │
│  │                                                               │  │
│  │  ┌─────────────────┐                                          │  │
│  │  │ BeforeInvocation│ ──► 钩子调用                             │  │
│  │  └────────┬────────┘                                          │  │
│  │           ▼                                                   │  │
│  │  ┌─────────────────┐                                          │  │
│  │  │ event_loop_cycle│ ─► 中间件链                              │  │
│  │  │                 │     ┌────────────────────────┐           │  │
│  │  │  ┌───────────┐ │     │ AgentStreamStage       │           │  │
│  │  │  │           │ │     │   ├── Input middleware  │           │  │
│  │  │  ▼           │ │     │   ├── Wrap middleware   │           │  │
│  │  │ ┌───────────┐│ │     │   └── Output middleware │           │  │
│  │  │ │ 模型推理  ││ │     └────────────────────────┘           │  │
│  │  │ └─────┬─────┘│ │                                          │  │
│  │  │       │     │ │     ┌────────────────────────┐           │  │
│  │  │       ▼     │ │     │ InvokeModelStage       │           │  │
│  │  │ ┌───────────┐│ │     │   ├── Input middleware  │           │  │
│  │  │ │ 停止原因? ││ │     │   ├── Wrap middleware   │           │  │
│  │  │ └─────┬─────┘│ │     │   └── Output middleware │           │  │
│  │  │       │     │ │     └────────────────────────┘           │  │
│  │  │  tool_use    │ │                                          │  │
│  │  │       │     │ │                                          │  │
│  │  │       ▼     │ │     ┌────────────────────────┐           │  │
│  │  │ ┌───────────┐│ │     │ ExecuteToolStage       │           │  │
│  │  │ │ 执行工具  ││ │     │   ├── Input middleware  │           │  │
│  │  │ │ (并发/顺序)│ │     │   ├── Wrap middleware   │           │  │
│  │  │ └─────┬─────┘│ │     │   └── Output middleware │           │  │
│  │  │       │     │ │     └────────────────────────┘           │  │
│  │  │       ▼     │ │                                          │  │
│  │  │ 递归调用    │ │                                          │  │
│  │  └───────────┘ │                                          │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌─────────────────────────┐                                         │
│  │   AfterInvocationEvent  │ ──► 钩子调用                          │
│  └─────────────────────────┘                                         │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│              AgentResult (stop_reason, message, metrics)              │
│                                                                      │
│  stop_reason: "end_turn" | "tool_use" | "max_tokens" |              │
│              "limit_turns" | "cancelled" | "checkpoint" | "interrupt" │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 14. 关键类图

```
┌─────────────────────────────────────────────────────────────────────┐
│                              Agent                                   │
├─────────────────────────────────────────────────────────────────────┤
│ - model: Model                                                      │
│ - messages: Messages                                                │
│ - tool_registry: ToolRegistry                                       │
│ - conversation_manager: ConversationManager                         │
│ - hooks: HookRegistry                                               │
│ - _middleware_registry: MiddlewareRegistry                          │
│ - tool_executor: ToolExecutor                                       │
│ - _retry_strategy: ModelRetryStrategy                              │
│ - tool_caller: _ToolCaller                                          │
│ - _interrupt_state: _InterruptState                                │
│ - _concurrency: _ConcurrencyController                             │
├─────────────────────────────────────────────────────────────────────┤
│ + __call__(prompt) → AgentResult                                    │
│ + invoke_async(prompt) → AgentResult                               │
│ + stream_async(prompt) → AsyncIterator                              │
│ + as_tool() → AgentTool                                             │
│ + cancel() → None                                                   │
│ + add_hook(callback, event_type) → None                            │
│ + tool: _ToolCaller (属性)                                          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ 组合
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐
│   ToolRegistry  │  │ ConversationManager│  │   MiddlewareRegistry│
├─────────────────┤  ├──────────────────┤  ├────────────────────┤
│ - registry      │  │ - window_size    │  │ - _handlers        │
│ - dynamic_tools │  │ - summary_ratio  │  ├────────────────────┤
│ - _tool_providers│ │ - _compression...│  │ + add_middleware() │
├─────────────────┤  ├──────────────────┤  │ + compose()       │
│ + process_tools │  │ + apply_mgmt()  │  │ + invoke()        │
│ + register_tool │  │ + reduce_context│  └────────────────────┘
│ + get_tool_specs│  └──────────────────┘
└─────────────────┘           │
                             │ 继承
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│ SlidingWindowCM    │  │ SummarizingCM       │  │ NullConversationMgr │
│ - window_size=40   │  │ - summary_ratio=0.3 │  │ (无操作)            │
│ - should_truncate  │  │ - preserve_recent   │  └────────────────────┘
└────────────────────┘  └────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                          ToolExecutor                                │
├─────────────────────────────────────────────────────────────────────┤
│ + _stream() [static]                                                │
│ + _stream_with_trace() [static]                                     │
│ # _execute() [abstract]                                             │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│ ConcurrentToolExecutor  │     │  SequentialToolExecutor  │
├─────────────────────────┤     ├─────────────────────────┤
│ # _execute()            │     │ # _execute()            │
│ (并发执行所有工具)        │     │ (顺序执行所有工具)        │
└─────────────────────────┘     └─────────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                           HookRegistry                              │
├─────────────────────────────────────────────────────────────────────┤
│ - _registered_callbacks: dict[type, list[_CallbackEntry]]          │
├─────────────────────────────────────────────────────────────────────┤
│ + add_callback(event_type, callback, order) → None                 │
│ + add_hook(hook: HookProvider) → None                              │
│ + invoke_callbacks_async(event) → tuple[event, interrupts]        │
│ + get_callbacks_for(event) → Generator                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 15. 总结

### 15.1 核心设计模式

1. **ReAct 模式 (Reasoning + Acting)**
   - 模型推理 → 决定是否使用工具 → 执行工具 → 循环

2. **中间件架构 (Middleware)**
   - 三层: Input / Wrap / Output
   - 可插拔、可组合

3. **事件驱动钩子 (Event-driven Hooks)**
   - 强类型事件系统
   - 支持同步/异步回调

4. **策略模式 (Strategy)**
   - 对话管理: 滑动窗口 / 总结
   - 工具执行: 并发 / 顺序

### 15.2 关键特性

| 特性 | 实现方式 |
|------|---------|
| 流式处理 | AsyncIterator + TypedEvent |
| 并发控制 | _ConcurrencyController |
| 重试机制 | Hook + 指数退避 |
| 上下文管理 | ConversationManager |
| 人工干预 | InterruptException |
| 检查点/恢复 | Checkpoint + EventLoopStopEvent |
| 追踪 | OpenTelemetry + custom tracer |

### 15.3 扩展点

1. **自定义工具**: 实现 `AgentTool` 接口
2. **自定义对话管理**: 继承 `ConversationManager`
3. **自定义钩子**: 实现 `HookProvider` 或 `HookCallback`
4. **自定义中间件**: 实现 `MiddlewareHandler` 接口
5. **自定义执行器**: 继承 `ToolExecutor`

### 15.4 执行流程总结

```
用户输入 → 消息转换 → 并发控制 
    ↓
事件循环 → 模型调用 → 中间件处理 → 流处理
    ↓                      ↓
中断检查 ← 工具决策 ← 模型响应
    ↓
工具执行 → 钩子调用 → 中间件处理
    ↓
递归循环 → 应用管理 → 结果输出
    ↓
对话管理 → AgentResult
```

---

## 参考文件

- `agent.py` - Agent 主类
- `event_loop.py` - 事件循环核心
- `streaming.py` - 流处理
- `tools/registry.py` - 工具注册
- `tools/_caller.py` - 工具调用
- `tools/executors/` - 工具执行器
- `conversation_manager/` - 对话管理器
- `hooks/registry.py` - 钩子系统
- `_middleware/` - 中间件系统
- `event_loop/_retry.py` - 重试策略
