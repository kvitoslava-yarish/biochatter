from unittest.mock import Mock, MagicMock, patch
import os
import uuid
import unittest

import pytest
import requests

from biochatter.llm_connect import GptConversation
from biochatter.api_agent.blast import (
    BLAST_QUERY_PROMPT,
    BLAST_SUMMARY_PROMPT,
    BlastFetcher,
    BlastInterpreter,
    BlastQueryBuilder,
    BlastQueryParameters,
)
from biochatter.api_agent.api_agent import APIAgent

from biochatter.llm_connect import GptConversation
from biochatter.api_agent.oncokb import (
    ONCOKB_QUERY_PROMPT,
    ONCOKB_SUMMARY_PROMPT,
    OncoKBFetcher,
    OncoKBInterpreter,
    OncoKBQueryBuilder,
    OncoKBQueryParameters,
)


def conversation_factory():
    conversation = GptConversation(
        model_name="gpt-4o",
        correct=False,
        prompts={},
    )
    conversation.set_api_key(os.getenv("OPENAI_API_KEY"), user="test")
    return conversation


class TestBlastQueryBuilder:
    @patch("biochatter.api_agent.blast.BlastQueryBuilder.create_runnable")
    def test_create_runnable(self, mock_create_runnable):
        # Arrange
        mock_runnable = MagicMock()
        mock_create_runnable.return_value = mock_runnable

        query_parameters = BlastQueryParameters()
        builder = BlastQueryBuilder()

        # Act
        result = builder.create_runnable(
            query_parameters=query_parameters, llm=None
        )

        # Assert
        mock_create_runnable.assert_called_once_with(
            query_parameters=query_parameters,
            llm=None,
        )
        assert result == mock_runnable

    @patch("biochatter.api_agent.blast.BlastQueryBuilder.create_runnable")
    @patch("biochatter.llm_connect.GptConversation")
    def test_parameterise_query(self, mock_conversation, mock_create_runnable):
        # Arrange
        mock_runnable = MagicMock()
        mock_create_runnable.return_value = mock_runnable

        mock_blast_query_parameters = MagicMock()
        mock_runnable.invoke.return_value = mock_blast_query_parameters

        question = "What is the sequence of the gene?"
        mock_conversation_instance = mock_conversation.return_value

        builder = BlastQueryBuilder()

        # Act
        result = builder.parameterise_query(
            question, mock_conversation_instance
        )

        # Assert
        mock_create_runnable.assert_called_once_with(
            query_parameters=BlastQueryParameters,
            conversation=mock_conversation_instance,
        )
        mock_runnable.invoke.assert_called_once_with(
            {"input": f"Answer:\n{question} based on:\n {BLAST_QUERY_PROMPT}"}
        )
        assert result == mock_blast_query_parameters
        assert hasattr(result, "question_uuid")


class TestBlastFetcher:
    @patch("biochatter.api_agent.blast.BlastFetcher.submit_query")
    def test_submit_query(self, mock_submit_query):
        # Arrange
        mock_response = MagicMock()
        mock_submit_query.return_value = mock_response

        query_parameters = BlastQueryParameters()
        fetcher = BlastFetcher()

        # Act
        result = fetcher.submit_query(query_parameters)

        # Assert
        mock_submit_query.assert_called_once_with(query_parameters)
        assert result == mock_response

    @patch("biochatter.api_agent.blast.BlastFetcher.fetch_and_save_results")
    def test_fetch_and_save_results(self, mock_fetch_and_save_results):
        # Arrange
        mock_response = MagicMock()
        mock_fetch_and_save_results.return_value = mock_response

        query_id = "test_query_id"
        fetcher = BlastFetcher()

        # Act
        result = fetcher.fetch_and_save_results(query_id)

        # Assert
        mock_fetch_and_save_results.assert_called_once_with(query_id)
        assert result == mock_response

    @patch("requests.post")
    def test_submit_query_value_error(self, mock_post):
        # Arrange
        mock_response = MagicMock()
        mock_response.text = "No RID found in this response"
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        query_parameters = BlastQueryParameters(
            cmd="some_cmd",
            program="some_program",
            database="some_database",
            query="some_query",
            format_type="some_format",
            megablast="some_megablast",
            max_hits=10,
            url="http://example.com/blast",
        )
        fetcher = BlastFetcher()

        # Act & Assert
        with pytest.raises(
            ValueError, match="RID not found in BLAST submission response."
        ):
            fetcher.submit_query(query_parameters)


@pytest.fixture
def mock_conversation():
    with patch("biochatter.llm_connect.GptConversation") as mock:
        yield mock


@pytest.fixture
def mock_output_parser():
    with patch("biochatter.api_agent.blast.StrOutputParser") as mock:
        mock_parser = MagicMock()
        mock.return_value = mock_parser
        yield mock_parser


@pytest.fixture
def mock_chain(mock_conversation, mock_output_parser):
    with patch(
        "biochatter.api_agent.blast.ChatPromptTemplate.from_messages"
    ) as mock_prompt:
        mock_prompt_instance = MagicMock()
        mock_prompt.return_value = mock_prompt_instance
        mock_chain = (
            mock_prompt_instance | mock_conversation.chat | mock_output_parser
        )
        yield mock_chain


class TestBlastInterpreter:
    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_file_not_found(self, mock_open):
        # Arrange
        mock_open.side_effect = FileNotFoundError
        interpreter = BlastInterpreter()

        # Act
        result = interpreter.read_first_n_lines("non_existent_file.txt", 5)

        # Assert
        assert result == "The file was not found."

    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_general_exception(self, mock_open):
        # Arrange
        mock_open.side_effect = Exception("Some error")
        interpreter = BlastInterpreter()

        # Act
        result = interpreter.read_first_n_lines("some_file.txt", 5)

        # Assert
        assert result == "An error occurred: Some error"

    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_success(self, mock_open):
        # Arrange
        mock_file = MagicMock()
        mock_file.readline.side_effect = [
            "line1\n",
            "line2\n",
            "line3\n",
            "",
            "",
        ]
        mock_open.return_value.__enter__.return_value = mock_file
        interpreter = BlastInterpreter()

        # Act
        result = interpreter.read_first_n_lines("some_file.txt", 5)

        # Assert
        assert result == "line1\nline2\nline3"

    def test_summarise_results(mock_prompt, mock_conversation, mock_chain):
        # Arrange
        interpreter = BlastInterpreter()
        question = "What is the best hit?"
        file_path = "test_blast_results.txt"
        n_lines = 10
        expected_context = "Mocked context from file"
        expected_summary_prompt = BLAST_SUMMARY_PROMPT.format(
            question=question, context=expected_context
        )
        expected_answer = "Mocked answer"

        # Mock the methods and functions
        interpreter.read_first_n_lines = MagicMock(
            return_value=expected_context
        )
        mock_chain.invoke = MagicMock(return_value=expected_answer)

        # Act
        result = interpreter.summarise_results(
            question, mock_conversation, file_path, n_lines
        )

        # Assert
        assert result == expected_answer
        interpreter.read_first_n_lines.assert_called_once_with(
            file_path, n_lines
        )
        mock_chain.invoke.assert_called_once_with(
            {"input": {expected_summary_prompt}}
        )


class TestOncoKBQueryBuilder:
    @patch("biochatter.api_agent.oncokb.OncoKBQueryBuilder.create_runnable")
    def test_create_runnable(self, mock_create_runnable):
        # Arrange
        mock_runnable = MagicMock()
        mock_create_runnable.return_value = mock_runnable

        query_parameters = OncoKBQueryParameters(
            endpoint="specific/endPoint/toHit",
            base_url="https://demo.oncokb.org/api/v1",
        )
        builder = OncoKBQueryBuilder()

        # Act
        result = builder.create_runnable(
            query_parameters=query_parameters, llm=None
        )

        # Assert
        mock_create_runnable.assert_called_once_with(
            query_parameters=query_parameters,
            llm=None,
        )
        assert result == mock_runnable

    @patch("biochatter.api_agent.oncokb.OncoKBQueryBuilder.create_runnable")
    @patch("biochatter.llm_connect.GptConversation")
    def test_parameterise_query(self, mock_conversation, mock_create_runnable):
        # Arrange
        mock_runnable = MagicMock()
        mock_create_runnable.return_value = mock_runnable

        mock_oncokb_query_parameters = MagicMock()
        mock_runnable.invoke.return_value = mock_oncokb_query_parameters

        question = "What is the sequence of the gene?"
        mock_conversation_instance = mock_conversation.return_value

        builder = OncoKBQueryBuilder()

        # Act
        result = builder.parameterise_query(
            question, mock_conversation_instance
        )

        # Assert
        mock_create_runnable.assert_called_once_with(
            query_parameters=OncoKBQueryParameters,
            conversation=mock_conversation_instance,
        )
        mock_runnable.invoke.assert_called_once_with(
            {"input": f"Answer:\n{question} based on:\n {ONCOKB_QUERY_PROMPT}"}
        )
        assert result == mock_oncokb_query_parameters
        assert hasattr(result, "question_uuid")


class TestOncoKBFetcher:
    @patch("biochatter.api_agent.oncokb.OncoKBFetcher.submit_query")
    def test_submit_query(self, mock_submit_query):
        # Arrange
        mock_response = MagicMock()
        mock_submit_query.return_value = mock_response

        query_parameters = OncoKBQueryParameters(
            endpoint="specific/endPoint/toHit",
            base_url="https://demo.oncokb.org/api/v1",
        )
        fetcher = OncoKBFetcher()

        # Act
        result = fetcher.submit_query(query_parameters)

        # Assert
        mock_submit_query.assert_called_once_with(query_parameters)
        assert result == mock_response

    @patch("biochatter.api_agent.oncokb.OncoKBFetcher.fetch_and_save_results")
    def test_fetch_and_save_results(self, mock_fetch_and_save_results):
        # Arrange
        mock_response = MagicMock()
        mock_fetch_and_save_results.return_value = mock_response

        query_id = "test_query_id"
        fetcher = OncoKBFetcher()

        # Act
        result = fetcher.fetch_and_save_results(query_id)

        # Assert
        mock_fetch_and_save_results.assert_called_once_with(query_id)
        assert result == mock_response


@pytest.fixture
def mock_conversation():
    with patch("biochatter.llm_connect.GptConversation") as mock:
        yield mock


@pytest.fixture
def mock_output_parser():
    with patch("biochatter.api_agent.oncokb.StrOutputParser") as mock:
        mock_parser = MagicMock()
        mock.return_value = mock_parser
        yield mock_parser


@pytest.fixture
def mock_chain(mock_conversation, mock_output_parser):
    with patch(
        "biochatter.api_agent.oncokb.ChatPromptTemplate.from_messages"
    ) as mock_prompt:
        mock_prompt_instance = MagicMock()
        mock_prompt.return_value = mock_prompt_instance
        mock_chain = (
            mock_prompt_instance | mock_conversation.chat | mock_output_parser
        )
        yield mock_chain


class TestOncoKBInterpreter:
    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_file_not_found(self, mock_open):
        # Arrange
        mock_open.side_effect = FileNotFoundError
        interpreter = OncoKBInterpreter()

        # Act
        result = interpreter.read_first_n_lines("non_existent_file.txt", 5)

        # Assert
        assert result == "The file was not found."

    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_general_exception(self, mock_open):
        # Arrange
        mock_open.side_effect = Exception("Some error")
        interpreter = OncoKBInterpreter()

        # Act
        result = interpreter.read_first_n_lines("some_file.txt", 5)

        # Assert
        assert result == "An error occurred: Some error"

    @patch("builtins.open", new_callable=MagicMock)
    def test_read_first_n_lines_success(self, mock_open):
        # Arrange
        mock_file = MagicMock()
        mock_file.readline.side_effect = [
            "line1\n",
            "line2\n",
            "line3\n",
            "",
            "",
        ]
        mock_open.return_value.__enter__.return_value = mock_file
        interpreter = OncoKBInterpreter()

        # Act
        result = interpreter.read_first_n_lines("some_file.txt", 5)

        # Assert
        assert result == "line1\nline2\nline3"

    def test_summarise_results(mock_prompt, mock_conversation, mock_chain):
        # Arrange
        interpreter = OncoKBInterpreter()
        question = "What is the best hit?"
        file_path = "test_oncokb_results.txt"
        n_lines = 10
        expected_context = "Mocked context from file"
        expected_summary_prompt = ONCOKB_SUMMARY_PROMPT.format(
            question=question, context=expected_context
        )
        expected_answer = "Mocked answer"

        # Mock the methods and functions
        interpreter.read_first_n_lines = MagicMock(
            return_value=expected_context
        )
        mock_chain.invoke = MagicMock(return_value=expected_answer)

        # Act
        result = interpreter.summarise_results(
            question, mock_conversation, file_path, n_lines
        )

        # Assert
        assert result == expected_answer
        interpreter.read_first_n_lines.assert_called_once_with(
            file_path, n_lines
        )
        mock_chain.invoke.assert_called_once_with(
            {"input": {expected_summary_prompt}}
        )
