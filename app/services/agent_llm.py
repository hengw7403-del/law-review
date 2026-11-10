"""Function Calling is isolated from the deterministic risk-judge JSON client."""
import time
from openai import APIStatusError
from app.services.llm import LLMClient, MODEL


class AgentLLMClient(LLMClient):
    def decide(self, messages, tools, context):
        if self.budget is not None:self.budget.reserve()
        client=self._get_client();context.attempt_no+=1
        self._begin_attempt(context)
        started=time.perf_counter();usage={'prompt_tokens':None,'completion_tokens':None};error=None;response=None
        try:
            response=client.chat.completions.create(model=MODEL,messages=messages,tools=tools,
                tool_choice='auto',temperature=0,max_tokens=1024,stream=False)
        except Exception as exc:error=exc
        duration_ms=round((time.perf_counter()-started)*1000)
        if response is not None and response.usage:
            usage={k:getattr(response.usage,k,None) for k in usage}
        code=('HTTP_'+str(error.status_code) if isinstance(error,APIStatusError) else type(error).__name__) if error else None
        self._record(context,usage,duration_ms,code,getattr(response,'model',MODEL))
        if error:raise error
        if not response.choices:raise ValueError('Agent模型没有返回选择')
        return response.choices[0].message.model_dump(exclude_none=True)
