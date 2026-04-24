import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # Tell client how to launch the server
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"]
    )

    # Connect via stdio
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. List available tools
            tools = await session.list_tools()
            print("Tools found:")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description.strip().split(chr(10))[0]}")

            # 2. Call the tool
            result = await session.call_tool(
                "extract_vitals",
                {"report": "Patient BP 140/90, HR 95."}
            )
            print("\nTool result:")
            print(result.content[0].text)

asyncio.run(main())