"""MCP 공개 스키마와 실제 CLI 파서 사이의 호환성 계약."""
import importlib.util
import unittest
from unittest.mock import patch


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(MCP_AVAILABLE, "MCP 계약 검증에는 선택 패키지 mcp가 필요합니다")
class MCPContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import mcp_server
        self.server = mcp_server

    async def test_fresh_is_optional_boolean_in_registered_schema(self):
        tools = {tool.name: tool for tool in await self.server.mcp.list_tools()}
        for name in ("search_kindergartens", "kindergarten_profile"):
            with self.subTest(tool=name):
                schema = tools[name].inputSchema
                self.assertEqual(schema["properties"]["fresh"]["type"], "boolean")
                self.assertIs(schema["properties"]["fresh"]["default"], False)
                self.assertNotIn("fresh", schema.get("required", []))

    async def test_search_options_reach_cli_with_old_and_fresh_calls(self):
        for options, expected_fresh in (({}, False), ({"fresh": False}, False),
                                        ({"fresh": True}, True)):
            with self.subTest(options=options):
                arguments = dict(region="11680", age=3, estab="사립",
                                 name="햇살", sort="size", no_web=True, **options)
                with patch.object(self.server.kinderinfo, "cmd_search") as handler:
                    await self.server.mcp.call_tool("search_kindergartens", arguments)
                handler.assert_called_once()
                parsed = handler.call_args.args[0]
                self.assertIs(parsed.fresh, expected_fresh)
                self.assertEqual((parsed.region, parsed.age, parsed.estab, parsed.name,
                                  parsed.sort, parsed.no_web),
                                 ("11680", 3, "사립", "햇살", "size", True))

    async def test_profile_fresh_preserves_web_and_extended_options(self):
        for options, expected_fresh in (({}, False), ({"fresh": False}, False),
                                        ({"fresh": True}, True)):
            for web, extended in ((False, False), (True, False), (False, True)):
                with self.subTest(options=options, web=web, extended=extended):
                    arguments = dict(region="11680", name="햇살", web=web,
                                     extended=extended, **options)
                    with patch.object(self.server.kinderinfo, "cmd_profile") as handler:
                        await self.server.mcp.call_tool("kindergarten_profile", arguments)
                    handler.assert_called_once()
                    parsed = handler.call_args.args[0]
                    self.assertIs(parsed.fresh, expected_fresh)
                    self.assertEqual((parsed.region, parsed.name, parsed.web, parsed.extended),
                                     ("11680", "햇살", web, extended))


if __name__ == "__main__":
    unittest.main()
