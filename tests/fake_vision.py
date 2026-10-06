"""Fake vision providers for tests."""

from app.extraction.providers import ModelOutput


class FakeVision:
    """Returns given values; `type_` is what the 'model' says the document is."""

    name, model = "fake", "fake-vision-1"

    def __init__(self, values: dict, type_: str, per_type: dict | None = None):
        self.values, self.type_, self.per_type = values, type_, per_type or {}
        self.calls = []

    def extract(self, images, prompt, schema):
        self.calls.append(prompt)
        names = schema["properties"]["fields"]["properties"]
        values = self.per_type.get(len(self.calls), self.values)
        fields = {n: {"value": values.get(n), "evidence": f"{n}: {values.get(n)}", "page": 1}
                  for n in names}
        return ModelOutput({"document_type": self.type_, "fields": fields,
                            "legibility_notes": None}, self.model, 1000, 200, 1.5)
