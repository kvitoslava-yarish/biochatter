from typing import Optional
import yaml
import json
import os
from biocypher._misc import sentencecase_to_pascalcase
from .llm_connect import GptConversation


class BioCypherPrompt:
    def __init__(self, schema_config_path: str):
        """

        Given a biocypher schema configuration, extract the entities and
        relationships, and for each extract their mode of representation (node
        or edge), properties, and identifier namespace. Using these data, allow
        the generation of prompts for a large language model, informing it of
        the schema constituents and their properties, to enable the
        parameterisation of function calls to a knowledge graph.

        Args:
            schema_config_path: Path to a biocypher schema configuration file.

        """
        # read the schema configuration
        with open(schema_config_path, "r") as f:
            schema_config = yaml.safe_load(f)

        # extract the entities and relationships: each top level key that has
        # a 'represented_as' key
        self.entities = {}
        self.relationships = {}
        for key, value in schema_config.items():
            # hacky, better with biocypher output
            name_indicates_relationship = (
                "interaction" in key.lower() or "association" in key.lower()
            )
            if "represented_as" in value:
                if (
                    value["represented_as"] == "node"
                    and not name_indicates_relationship
                ):
                    self.entities[sentencecase_to_pascalcase(key)] = value
                elif (
                    value["represented_as"] == "node"
                    and name_indicates_relationship
                ):
                    self.relationships[sentencecase_to_pascalcase(key)] = value
                elif value["represented_as"] == "edge":
                    self.relationships[sentencecase_to_pascalcase(key)] = value

        self.question = ""
        self.selected_entities = []
        self.selected_relationships = []
        self.selected_relationship_labels = []  # copy to deal with labels that
        # are not the same as the relationship name

    def select_entities(self, question: str) -> bool:
        """

        Given a question, select the entities that are relevant to the question
        and store them in `selected_entities` and `selected_relationships`. Use
        LLM conversation to do this.

        Args:
            question: A user's question.

        Returns:
            True if at least one entity was selected, False otherwise.

        """

        self.question = question

        conversation = GptConversation(
            model_name="gpt-3.5-turbo",
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="entity_selector"
        )

        conversation.append_system_message(
            (
                "You have access to a knowledge graph that contains "
                f"these entities: {', '.join(self.entities)} and these "
                f"relationships: {', '.join(self.relationships)}. Your task is "
                "to select the ones that are relevant to the user's question "
                "for subsequent use in a query. Only return the entities and "
                "relationships, comma-separated, without any additional text. "
                "If you select relationships, make sure to also return "
                "entities that are connected by those relationships."
            )
        )

        msg, token_usage, correction = conversation.query(question)

        result = msg.split(",") if msg else []
        # TODO: do we go back and retry if no entities were selected? or ask for
        # a reason? offer visual selection of entities and relationships by the
        # user?

        if result:
            for entity_or_relationship in result:
                if entity_or_relationship in self.entities:
                    self.selected_entities.append(entity_or_relationship)
                elif entity_or_relationship in self.relationships:
                    self.selected_relationships.append(entity_or_relationship)
                    self.selected_relationship_labels.append(
                        self.relationships[entity_or_relationship].get(
                            "label_as_edge", entity_or_relationship
                        )
                    )

        return bool(result)

    def select_properties(
        self,
        question: Optional[str] = None,
        entities: Optional[list] = None,
        relationships: Optional[list] = None,
    ):
        """

        Given a question (optionally provided, but in the standard use case
        reused from the entity selection step) and the selected entities, select
        the properties that are relevant to the question and store them in
        the dictionary `selected_properties`.

        Args:
            question (Optional[str]): A user's question.

        Returns:
            True if at least one property was selected, False otherwise.

        """

        question = question or self.question

        if not question:
            raise ValueError(
                "No question provided, and no question from entity selection "
                "step available. Please provide a question or run the "
                "entity selection (`select_entities()`) step first."
            )

        entities = entities or self.selected_entities
        relationships = relationships or self.selected_relationships

        # raise error if not at least one of entities or relationships exists
        if not entities and not relationships:
            raise ValueError(
                "No entities or relationships provided, and none available "
                "from entity selection step. Please provide "
                "entities/relationships or run the entity selection "
                "(`select_entities()`) step first."
            )

        # subset the entities and relationships dictionaries to only the used
        # keys and only the property value
        e_props = {}
        for entity in entities:
            if self.entities[entity].get("properties"):
                e_props[entity] = list(
                    self.entities[entity]["properties"].keys()
                )

        r_props = {}
        for relationship in relationships:
            if self.relationships[relationship].get("properties"):
                r_props[relationship] = list(
                    self.relationships[relationship]["properties"].keys()
                )

        # TODO: split into separate prompts for entities and relationships,
        # return single JSON each

        msg = (
            "You have access to a knowledge graph that contains entities and "
            "relationships. They have the following properties: "
            f"{e_props} and {r_props}. Your task is to select the properties "
            "that are relevant to the user's question for subsequent use in a "
            "query. Only return the entities and relationships and relevant "
            "properties in JSON format, without any additional text. Do not "
            "return properties that are not relevant to the question."
        )

        conversation = GptConversation(
            model_name="gpt-3.5-turbo",
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="property_selector"
        )

        conversation.append_system_message(msg)

        msg, token_usage, correction = conversation.query(question)

        self.selected_properties = json.loads(msg) if msg else {}

        return bool(self.selected_properties)
