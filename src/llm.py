import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

from src.models import AgentUnavailableError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / '.env')


class OpenAIInspectionClient:

    def __init__(self, prompt_path):
        self._client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'),
                              base_url=os.environ.get('OPENAI_BASE_URL') or None)
        self._model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
        prompt_file = Path(prompt_path)
        if not prompt_file.is_absolute():
            prompt_file = PROJECT_ROOT / prompt_file
        with open(prompt_file, 'r', encoding='utf-8') as handle:
            self._prompt_template = handle.read()
        code_path = PROJECT_ROOT / 'data' / 'code_sections.json'
        with open(code_path, 'r', encoding='utf-8') as handle:
            self._code_sections = json.load(handle)

    def respond(self, item):
        code_context = '\n'.join(
            f'- {section}: {self._code_sections.get(section, "No description provided")}'
            for section in item.applicable_code_sections
        )
        prompt = self._prompt_template.format(
            item_id=item.id,
            category=item.category,
            observation=item.observation,
            applicable_code_sections=code_context,
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0,
            )
        except OpenAIError as exc:
            raise AgentUnavailableError('The inspection model is unavailable') from exc
        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return {}
