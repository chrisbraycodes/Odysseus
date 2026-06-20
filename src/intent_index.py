"""
Semantic intent routing for workspace agent actions.

Embeds example utterances per action type in ChromaDB (same lane pattern as
tool_index.py) so natural-language requests route to the right orchestrator
without exact keyword phrasing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
    dedupe_results,
    migrate_legacy_collection,
)

logger = logging.getLogger(__name__)

COLLECTION_NAME = "odysseus_intent_index"

# action_id -> list of natural-language example utterances
ACTION_UTTERANCES: Dict[str, List[str]] = {
    "create_files": [
        "create ten text files numbered one through ten",
        "make a bunch of txt files with numbers inside them",
        "write files 1.txt 2.txt 3.txt with descending counts",
        "generate numbered files in the workspace folder",
        "add some new files to my project with specific content",
        "create multiple files at once with different names",
        "make files named 1.txt 2.txt and put numbers in each",
        "save ten documents as plain text files",
        "produce a file called config.json with this content",
        "output a new script called setup.sh",
        "put together three log files named a b and c",
        "dump the data into a file called results.txt",
        "touch a new empty file called placeholder.txt",
        "give me a file called notes.md with these bullet points",
        "build me a file structure with these three docs",
        "spin up a file called index.html with boilerplate",
        "I want five files each containing a different greeting",
        "new file please called readme.txt with a short description",
        "set up a data.csv file with these headers",
        "write out ten notes numbered one to ten",
    ],
    "analyze_repo": [
        "analyze this project and explain the architecture",
        "what is this codebase about",
        "scan the whole workspace and summarize how it works",
        "tell me about this repository structure",
        "how does this app work end to end",
        "review this project and describe the main components",
        "what technologies are used in this folder",
        "give me an overview of this project",
        "walk me through the codebase",
        "examine this repo and tell me how it fits together",
        "investigate what this workspace does",
        "check out the project and explain the main pieces",
        "go through this code and break down the architecture",
        "look at the workspace and describe what each part does",
        "dig into this project and tell me the key modules",
        "can you understand this codebase for me",
        "what is the structure of this app",
        "break down how this repository is organized",
        "I need to understand what this project does",
        "explain the folder structure and what each part is for",
    ],
    "locate_code": [
        "which file handles authentication",
        "where is the login route defined",
        "find the code responsible for saving files",
        "what file contains the main entry point",
        "locate where websocket connections are set up",
        "figure out which module does routing",
        "show me where the database connection is configured",
        "point me to the file that handles errors",
        "where can I find the API client code",
        "which module is in charge of sending emails",
        "search for where user sessions are managed",
        "hunt down the config loader",
        "what handles the file upload logic",
        "where does the app boot up",
        "find where environment variables are read",
        "which file exports the main component",
        "track down the middleware that validates tokens",
        "where is the rate limiter implemented",
    ],
    "scaffold_project": [
        "scaffold a new react app in this workspace",
        "create a new node project here",
        "npx create-react-app my app",
        "initialize a python project with a virtualenv",
        "set up a new vite typescript project",
        "bootstrap a fresh web app in this folder",
        "kickstart a new express api project",
        "generate boilerplate for a fastapi service",
        "start a new project from a template",
        "create a starter repo for a vue app",
        "make a fresh django project here",
        "init a new typescript library",
        "set me up with a next.js project skeleton",
    ],
    "run_shell": [
        "run npm install in the project",
        "execute git status in the terminal",
        "start the dev server",
        "run the tests",
        "cd into the folder and list files",
        "build the docker image",
        "launch the application",
        "kick off the test suite",
        "fire up the server",
        "spin up the development environment",
        "install the packages",
        "run the build script",
        "execute the migration",
        "do a git pull",
        "check what branch I am on",
        "show me the running processes",
    ],
}

# Regex hints when embeddings are unavailable (same spirit as tool_index hints).
_KEYWORD_HINTS: Dict[frozenset, str] = {
    frozenset({
        "create", "make", "write", "generate", "add", "save",
        "produce", "output", "dump", "touch", "put",
        "txt", "text file", "file", "files", "numbered", "doc", "log", "note", "script",
    }): "create_files",
    frozenset({
        "analyze", "analyse", "architecture", "codebase", "how does this",
        "what is this project", "scan", "repo structure", "overview",
        "examine", "investigate", "walk through", "check out", "dig into",
        "look at", "break down", "understand", "workspace",
    }): "analyze_repo",
    frozenset({
        "which file", "what file", "where is", "where does",
        "find the file", "locate", "handles", "responsible for",
        "show me where", "point me to", "where can i find",
        "which module", "hunt down", "track down", "search for",
    }): "locate_code",
    frozenset({
        "scaffold", "create-react-app", "npx create", "npm init",
        "bootstrap", "initialize project", "new react", "new vite",
        "kickstart", "boilerplate", "starter", "template", "skeleton",
        "fresh project", "new project",
    }): "scaffold_project",
    frozenset({
        "npm install", "npm run", "npx", "git", "docker",
        "run tests", "dev server", "terminal", "execute",
        "launch", "kick off", "fire up", "spin up", "install packages",
        "run script", "build script", "migration", "git pull",
    }): "run_shell",
}


@dataclass(frozen=True)
class IntentMatch:
    action: str
    score: float
    source: str  # "semantic" | "keyword"


class IntentIndex:
    """ChromaDB-backed semantic router for high-level workspace actions."""

    def __init__(self):
        self._lanes = build_embedding_lanes(COLLECTION_NAME)
        if not self._lanes:
            raise RuntimeError("No embedding lanes available for intent index")
        migrate_legacy_collection(self._lanes, COLLECTION_NAME)
        self._fingerprint = ""
        self._healthy = True

    @property
    def healthy(self) -> bool:
        return self._healthy and any(lane.healthy for lane in self._lanes)

    def index_actions(self) -> None:
        docs: List[str] = []
        ids: List[str] = []
        metadatas: List[Dict[str, str]] = []
        for action, utterances in ACTION_UTTERANCES.items():
            for i, text in enumerate(utterances):
                docs.append(f"Action: {action}\nExample: {text}")
                ids.append(f"intent_{action}_{i}")
                metadatas.append({"action": action, "utterance": text[:200]})
        if not docs:
            return
        indexed = False
        for lane in self._lanes:
            try:
                lane.collection.upsert(
                    ids=ids,
                    documents=docs,
                    embeddings=lane.encode(docs),
                    metadatas=metadatas,
                )
                indexed = True
            except Exception as e:
                logger.warning("Intent indexing failed in %s lane: %s", lane.name, e)
        if not indexed:
            self._healthy = False
            raise RuntimeError("Intent indexing failed in all embedding lanes")
        self._fingerprint = hashlib.sha256(
            ",".join(sorted(ACTION_UTTERANCES.keys())).encode()
        ).hexdigest()
        logger.info("Indexed %d intent utterances across %d actions", len(docs), len(ACTION_UTTERANCES))

    def retrieve(self, query: str, k: int = 5) -> List[Tuple[str, float]]:
        """Return (action_id, score) pairs sorted by relevance."""
        rows: List[Dict] = []
        lane_priority = {LANE_CUSTOM: 0, LANE_FASTEMBED: 1}
        for lane in self._lanes:
            try:
                count = lane.count()
                if count == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([query]),
                    n_results=min(k * 2, count),
                    include=["metadatas", "distances"],
                )
                if not results or not results.get("metadatas"):
                    continue
                distances = results.get("distances") or []
                for list_idx, meta_list in enumerate(results["metadatas"]):
                    distance_list = distances[list_idx] if list_idx < len(distances) else []
                    for idx, meta in enumerate(meta_list):
                        action = (meta or {}).get("action", "")
                        if not action:
                            continue
                        distance = distance_list[idx] if idx < len(distance_list) else 1.0
                        rows.append({
                            "action": action,
                            "score": round(1.0 - distance, 4),
                            "embedding_lane": lane.name,
                        })
            except Exception as e:
                logger.warning("Intent retrieval failed in %s lane: %s", lane.name, e)
        rows.sort(key=lambda row: (-row["score"], lane_priority.get(row["embedding_lane"], 99)))
        deduped = dedupe_results(rows, id_key="action", limit=k)
        return [(row["action"], row["score"]) for row in deduped]

    def _keyword_match(self, query: str) -> Optional[IntentMatch]:
        ql = (query or "").lower()
        if not ql.strip():
            return None
        best_action = None
        best_hits = 0
        for keywords, action in _KEYWORD_HINTS.items():
            hits = sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", ql))
            if hits > best_hits:
                best_hits = hits
                best_action = action
        if best_action and best_hits >= 2:
            return IntentMatch(best_action, 0.55 + 0.05 * min(best_hits, 4), "keyword")
        return None

    def match(self, query: str, *, min_score: float = 0.42) -> Optional[IntentMatch]:
        """Best action for a user message, or None if uncertain."""
        if not query or not query.strip():
            return None
        try:
            hits = self.retrieve(query, k=3)
            if hits and hits[0][1] >= min_score:
                return IntentMatch(hits[0][0], hits[0][1], "semantic")
        except Exception as e:
            logger.debug("Intent semantic match skipped: %s", e)
        return self._keyword_match(query)


def semantic_action_match(query: str, action: str, *, min_score: float = 0.42) -> bool:
    """True when semantic (or keyword) routing selects the given action."""
    idx = get_intent_index()
    if not idx:
        return False
    m = idx.match(query, min_score=min_score)
    return m is not None and m.action == action


_intent_index: Optional[IntentIndex] = None
_last_attempt = 0.0
_RETRY_INTERVAL = 30.0


def get_intent_index() -> Optional[IntentIndex]:
    """Lazy singleton; returns None if embeddings/Chroma unavailable."""
    global _intent_index, _last_attempt
    if _intent_index is not None and _intent_index.healthy:
        return _intent_index
    now = time.monotonic()
    if now - _last_attempt < _RETRY_INTERVAL:
        return None
    _last_attempt = now
    try:
        _intent_index = IntentIndex()
        _intent_index.index_actions()
        return _intent_index
    except Exception as e:
        logger.warning("IntentIndex init failed (retry in %ss): %s", _RETRY_INTERVAL, e)
        _intent_index = None
        return None


def reset_intent_index() -> None:
    global _intent_index, _last_attempt
    _intent_index = None
    _last_attempt = 0.0
