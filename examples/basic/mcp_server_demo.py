"""Example: Model Context Protocol (MCP) server with dual-use tools.

Run this script directly to test stdio or run with SSE:
    python examples/mcp_server_demo.py
"""

import sys
from pydantic import BaseModel, Field
from common_framework import create_mcp_server, tool, global_tool_registry


# 1. You can declare tools independently with @tool
@tool(name="calculate_bmi", description="Calculates Body Mass Index (BMI) given weight in kg and height in meters.")
def calculate_bmi(weight_kg: float, height_m: float) -> dict[str, float | str]:
    if height_m <= 0:
        raise ValueError("Height must be greater than 0")
    bmi = round(weight_kg / (height_m ** 2), 2)
    category = "Normal" if 18.5 <= bmi < 25 else "Underweight" if bmi < 18.5 else "Overweight"
    return {"bmi": bmi, "category": category}


# 2. Instantiate the Framework MCP Server
mcp_server = create_mcp_server(
    name="health-analytics-mcp",
    version="0.2.0-alpha",
    instructions="Provides analytical tools for health and fitness metrics.",
)

# 3. Add standalone tool to server (dual-use)
mcp_server.add_tool(calculate_bmi)


# 4. Or register tools directly on the server
@mcp_server.tool(name="heart_rate_zone", description="Determines target heart rate training zone based on max heart rate.")
def heart_rate_zone(age: int, current_bpm: int) -> dict[str, str | int]:
    max_hr = 220 - age
    percentage = round((current_bpm / max_hr) * 100)
    zone = (
        "Zone 1 (Warm up)" if percentage < 60
        else "Zone 2 (Fat Burn)" if percentage < 70
        else "Zone 3 (Aerobic)" if percentage < 80
        else "Zone 4 (Anaerobic)" if percentage < 90
        else "Zone 5 (Max Effort)"
    )
    return {"max_hr": max_hr, "current_percentage": percentage, "zone": zone}


if __name__ == "__main__":
    print(f"=== {mcp_server.name} (v{mcp_server.version}) ===")
    print("Registered dual-use tools in registry:")
    for t in global_tool_registry.list_tools():
        print(f" - {t.name}: {t.description}")

    # To run as standard MCP server over stdio (e.g. for Cursor, Claude, Antigravity):
    if len(sys.argv) > 1 and sys.argv[1] == "--stdio":
        print("\nStarting stdio transport for MCP client...")
        mcp_server.run_stdio()
    elif len(sys.argv) > 1 and sys.argv[1] == "--sse":
        print("\nStarting SSE transport on http://127.0.0.1:8000 ...")
        mcp_server.run_sse(port=8000)
    else:
        print("\nUsage:")
        print("  python examples/mcp_server_demo.py --stdio  # Run via stdio for MCP clients")
        print("  python examples/mcp_server_demo.py --sse    # Run via SSE for HTTP/Web clients")
