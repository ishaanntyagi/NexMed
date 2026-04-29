"""
Convert MCP tool definitions to Groq's tool-calling format.
"""

def mcp_to_groq_tools(mcp_tools):
    """
    Take MCP tools list (from session.list_tools()), return Groq-shaped list.

    MCP shape:    {name, description, inputSchema}
    Groq shape:   {type: "function", function: {name, description, parameters}}
    """
    groq_tools = []
    for t in mcp_tools:
        groq_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description.strip(),
                "parameters": t.inputSchema,
            }
        })
    return groq_tools


def filter_tools(groq_tools, allowed_names):
    """Keep only tools in the allowed list. Used to scope per agent."""
    return [t for t in groq_tools if t["function"]["name"] in allowed_names]