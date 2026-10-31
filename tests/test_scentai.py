"""Knowledge retrieval and import safety, without AI/network calls."""
import asyncio
import io
from unittest.mock import patch

import pytest
from app.core.ai_knowledge import (KnowledgeError, extract_file, fetch_link, select_context)



def test_context_retrieves_passage_deep_inside_long_document():
    items = [{'kind': 'ministry', 'id': 1, 'title': 'History',
              'text': ('ordinary text ' * 20000) + 'SpecificAbakaliki history of the ministry.'}]
    assert any('SpecificAbakaliki' in item['text'] for item in select_context(items, 'SpecificAbakaliki', budget=8000))


def test_text_and_word_tables_import():
    from docx import Document
    doc = Document(); doc.add_paragraph('Ministry history')
    doc.add_table(rows=1, cols=1).cell(0, 0).text = 'Visitor information'
    raw = io.BytesIO(); doc.save(raw)
    text = extract_file('knowledge.docx', raw.getvalue())
    assert 'Ministry history' in text and 'Visitor information' in text
    assert extract_file('facts.txt', b'Official ministry information') == 'Official ministry information'


@pytest.mark.parametrize('url', ['http://example.org', 'https://user:password@example.org', 'https://example.org:8443'])
def test_links_must_use_public_https(url):
    with pytest.raises(KnowledgeError):
        asyncio.run(fetch_link(url))


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_link_import_blocks_internal_addresses(address):
    with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
        with pytest.raises(KnowledgeError):
            asyncio.run(fetch_link('https://example.org'))


def test_pdf_text_import():
    from reportlab.pdfgen.canvas import Canvas
    raw = io.BytesIO()
    canvas = Canvas(raw)
    canvas.drawString(50, 750, "Official ministry visitor information.")
    canvas.save()
    assert "Official ministry visitor information." in extract_file("visitor.pdf", raw.getvalue())
