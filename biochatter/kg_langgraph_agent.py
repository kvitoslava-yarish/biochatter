from datetime import datetime
from typing import Callable, Dict, Optional, Any, List
import logging
import json
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)
from langchain_core.pydantic_v1 import (
    BaseModel,
    Field,
)
from langchain.output_parsers.openai_tools import (
    PydanticToolsParser,
    JsonOutputToolsParser,
)
from langchain_openai import ChatOpenAI
import neo4j_utils as nu

from biochatter.langgraph_agent_base import (
    ReflexionAgent,
    ResponderWithRetries,
    END_NODE,
    EXECUTE_TOOL_NODE
)

logger = logging.getLogger(__name__)

SEARCH_QUERIES = "search_queries"
SEARCH_QUERIES_DESCRIPTION = "query for genomicKB graph database"
REVISED_QUERY = "revised_query"
REVISED_QUERY_DESCRIPTION = "Revised query"

class GenerateQuery(BaseModel):
    """Generate the query."""
    answer: str = Field(description="Cypher query according to user's question.")
    reflection: str = Field(description="Your reflection on the initial answer, critique of what to improve")
    search_queries: List[str] = Field(
        description=SEARCH_QUERIES_DESCRIPTION
    )

class ReviseQuery(GenerateQuery):
    """Revise your previous query according to your question."""
    revised_query: str = Field(
        description=REVISED_QUERY_DESCRIPTION
    )

class KGQueryReflexionAgent(ReflexionAgent):

    def __init__(
        self, 
        conversation_factory: Callable,
        connection_args: Dict[str, str],
        query_lang: Optional[str] = "Cypher",
        max_steps: Optional[int] = 20,
    ):
        super().__init__(conversation_factory, max_steps)
        self.actor_prompt_template = ChatPromptTemplate.from_messages(
            [(
                "system",
                (
                    "As a senior biomedical researcher and graph database expert, "
                    f"your task is to generate '{query_lang}' queries to extract data from our genomicKB graph database based on the user's question. "
                    """Current time {time}. {instruction}"""
                ),
            ), 
            MessagesPlaceholder(variable_name="messages"), (
                "system",
                "Only generate query according to the user's question above."
            )]
        ).partial(time=lambda: datetime.now().isoformat())
        self.parser = JsonOutputToolsParser(return_id=True)
        self.connection_args = connection_args
        self.neodriver = None

    def _connect_db(self):
        if self.neodriver is not None:
            return
        try:
            db_uri = "bolt://" + self.connection_args.get("host") + \
                ":" + self.connection_args.get("port")
            self.neodriver = nu.Driver(
                db_name=self.connection_args.get("db_name") or "neo4j",
                db_uri=db_uri,
            )
        except Exception as e:
            logger.error(e)

    def _query_graph_database(self, query: str):
        self._connect_db()
        try:
            return self.neodriver.query(query)
        except Exception as e:
            logger.error(str(e))
            return [] # empty result

    def _create_initial_responder(self, prompt: Optional[str]=None) -> ResponderWithRetries:
        llm: ChatOpenAI = self.conversation.chat
        initial_chain = self.actor_prompt_template.partial(
            instruction=prompt if prompt is not None else ""
        ) | llm.bind_tools(
            tools=[GenerateQuery],
            tool_choice="GenerateQuery",
        )
        validator = PydanticToolsParser(tools=[GenerateQuery])
        return ResponderWithRetries(
            runnable=initial_chain,
            validator=validator
        )
    def _create_revise_responder(self, prompt: str | None = None) -> ResponderWithRetries:
        revision_instruction = """
Revise you previous query using the query result and follow the guidelines:
1. if you consistently obtain empty result, please consider removing constraints, like relationship constraint to try to obtain some results.
2. you should use previous critique to improve your query.
"""
        llm: ChatOpenAI = self.conversation.chat
        revision_chain = self.actor_prompt_template.partial(
            instruction=revision_instruction
        ) | llm.bind_tools(
            tools=[ReviseQuery],
            tool_choice="ReviseQuery",
        )
        validator = PydanticToolsParser(tools=[ReviseQuery])
        return ResponderWithRetries(
            runnable=revision_chain,
            validator=validator
        )
    
    def _tool_function(self, state: List[BaseMessage]):
        tool_message: AIMessage = state[-1]
        parsed_tool_messages = self.parser.invoke(tool_message)
        results = []
        for parsed_message in parsed_tool_messages:
            try:
                parsed_args = parsed_message["args"]
                query = (parsed_args[REVISED_QUERY] 
                         if REVISED_QUERY in parsed_args
                         else (parsed_args[REVISED_QUERY_DESCRIPTION] 
                               if REVISED_QUERY_DESCRIPTION in parsed_args 
                               else None))
                if query is not None:
                    result = self._query_graph_database(query)
                    results.append({
                        "query": query,
                        "result": result[0]
                    })
                    continue
                queries = (parsed_args[SEARCH_QUERIES] 
                           if SEARCH_QUERIES in parsed_args
                           else parsed_args[SEARCH_QUERIES_DESCRIPTION])
                for query in queries:
                    result = self._query_graph_database(query)
                    results.append({
                        "query": query,
                        "result": result[0]
                    })
            except Exception as e:
                logger.error(f"Error occurred: {str(e)}")
        
        content = None
        if len(results) > 1:
            # If there are multiple reusults, we only return
            # the first non-empty result
            for res in results:
                if res["result"] and len(res["result"]) > 0:
                    content=json.dumps(res)
        if content is None:
            content = json.dumps(results[0]) if len(results) > 0 else ""
        return ToolMessage(
            content=content,
            tool_call_id=parsed_message["id"],
        )
    
    @staticmethod
    def _get_last_tool_results_num(state: List[BaseMessage]):
        i = 0
        for m in state[::-1]:
            if not isinstance(m, ToolMessage):
                continue
            message: ToolMessage = m
            logger.info(f"query result: {message.content}")
            results = json.loads(message.content)
            empty = True
            if len(results["result"]) > 0:
                # check if it is really not empty, remove the case: {"result": [{"c.name": None}]}
                for res in results["result"]:
                    for k in res.keys():
                        if res[k] is None:
                            continue
                        if isinstance(res[k], str) and (res[k] == "None" or res[k] == "null"):
                            continue
                        empty = False
                        break
                    if not empty:
                        break
            return len(results["result"]) if not empty else 0
        
        return 0

    def _should_continue(self, state: List[BaseMessage]):
        res = super()._should_continue(state)
        if res == END_NODE:
            return res
        query_results_num = KGQueryReflexionAgent._get_last_tool_results_num(state)
        return (END_NODE if query_results_num > 0 
                else EXECUTE_TOOL_NODE)
    
    def _log_step_message(self, step: int, node: str, output: BaseMessage):
        try:
            parsed_output = self.parser.invoke(output)
            self._log_message(f"## {step}, {node}")
            self._log_message(
                f'Answer: {parsed_output[0]["args"]["answer"]}'
            )
            self._log_message(
                f'Reflection | Improving: {parsed_output[0]["args"]["reflection"]}')
            self._log_message('Reflection | Search Queries:')
            for i, sq in enumerate(parsed_output[0]["args"][SEARCH_QUERIES]):
                self._log_message(f"{i+1}: {sq}")
            if REVISED_QUERY in parsed_output[0]["args"]:
                self._log_message("Reflection | Revised Query:")
                self._log_message(parsed_output[0]["args"][REVISED_QUERY])
            self._log_message("-------------------------------- Node Output --------------------------------")
        except Exception as e:
            self._log_message(str(output)[:100] + " ...", "error")

    def _parse_final_result(self, output: BaseMessage) -> str | None:
        return self.parser.invoke(output)[0]["args"]["answer"]
    
    def _log_final_result(self, output: BaseMessage):
        self._log_message("\n\n-------------------------------- Final Generated Response --------------------------------")
        final_result = self._parse_final_result(output)
        self._log_message(final_result)



