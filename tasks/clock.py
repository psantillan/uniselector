"""A clock for a baseline agent: one tool, `sleep`, on a Model Context Protocol server on stdio.

  python -m uniselector_tasks.clock

A baseline agent has no Uniselector. With this tool it can wait between two readings, so the
baseline is an agent that waits by itself, and not an agent that reads as fast as it can.
"""
import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

TOOL = types.Tool(name="sleep", description="Wait for a number of seconds, then return. Use it between two readings of something that you watch.",
                  input_schema={"type": "object", "required": ["seconds"], "properties": {"seconds": {"type": "number", "minimum": 0}}})


def server() -> Server:
    async def list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[TOOL])

    async def call_tool(context, params) -> types.CallToolResult:
        seconds = (params.arguments or {}).get("seconds")
        if params.name != "sleep" or not isinstance(seconds, (int, float)) or seconds < 0:
            return types.CallToolResult(content=[types.TextContent(type="text", text="sleep needs seconds: a number, 0 or more")], is_error=True)
        await anyio.sleep(seconds)
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{seconds} seconds are over")])

    return Server("clock", on_list_tools=list_tools, on_call_tool=call_tool)


def main() -> None:
    async def run() -> None:
        clock = server()
        async with stdio_server() as (read, write):
            await clock.run(read, write, clock.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
