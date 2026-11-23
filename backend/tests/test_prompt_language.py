from tracelens.prompt_language import localize_messages, localize_model, localize_text


def test_chinese_prompt_routing_preserves_protocol_terms():
    text = localize_text('Return JSON with taxonomy_tag and the Agent step.', 'zh')
    assert '请用中文理解' in text
    assert 'taxonomy_tag' in text and 'Agent' in text

    messages = localize_messages([{'role': 'system', 'content': 'Analyze the trajectory.'}], 'zh')
    assert messages[0]['role'] == 'system'
    assert '请用中文理解' in messages[0]['content']
    assert localize_text('Analyze', 'en') == 'Analyze'


def test_model_boundary_localizes_both_openai_call_shapes():
    class FakeModel:
        def __init__(self):
            self.prompts = []

        def generate(self, query, *args, **kwargs):
            self.prompts.append(query)
            return query

        def generate_chat(self, messages, *args, **kwargs):
            self.prompts.append(messages)
            return messages

    model = localize_model(FakeModel(), 'zh')
    model.generate('compress this')
    model.generate_chat([{'role': 'user', 'content': 'diagnose this'}])
    assert '请用中文理解' in model.prompts[0]
    assert model.prompts[1][0]['role'] == 'system'
    assert '请用中文理解' in model.prompts[1][0]['content']
