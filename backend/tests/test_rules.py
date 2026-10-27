import pytest
from app.ai.rules import RULES, Reply, plan
from app.ai.nlu import Context


@pytest.mark.parametrize('rule,example', [(rule, example) for rule in RULES for example in rule.examples], ids=lambda x: getattr(x, 'id', getattr(x, 'say', 'example')))
def test_declared_rule_examples(rule, example):
    context = Context.from_messages([{'role': 'assistant', 'content': example.context}]) if example.context else Context()
    result, matched = plan(example.say, 'inventory', context)
    assert matched and matched.id == rule.id
    if example.reply:
        assert isinstance(result, Reply) and example.reply in result.text
    else:
        assert [(call.tool, call.args) for call in result] == list(example.calls)
