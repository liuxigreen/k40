from official_watch import _diff_openapi


def test_diff_openapi_detects_added_removed_paths_and_hints():
    prev = {
        'path_count': 2,
        'paths': ['/api/forum', '/api/agents/feed'],
        'methods': {'/api/forum': ['get'], '/api/agents/feed': ['get']},
        'schemas': {'FeedResponse': {'properties': ['items']}},
    }
    cur = {
        'path_count': 3,
        'paths': ['/api/forum', '/api/red-packets', '/api/agents/feed'],
        'methods': {'/api/forum': ['get', 'post'], '/api/red-packets': ['get'], '/api/agents/feed': ['get']},
        'schemas': {'FeedResponse': {'properties': ['items', 'meta']}, 'PacketResponse': {'properties': ['id']}},
    }
    diff = _diff_openapi(prev, cur)
    assert '/api/red-packets' in diff['added_paths']
    assert diff['removed_paths'] == []
    assert any('tasks.redpacket' in hint for hint in diff['impact_hints'])
    assert diff['added_methods']['/api/forum'] == ['post']
    assert diff['schema_changes']['FeedResponse']['added_properties'] == ['meta']
    assert diff['added_schemas'] == ['PacketResponse']
