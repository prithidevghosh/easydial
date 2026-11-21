import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from openai import OpenAIError

from .controller import DomainError, Session, SessionStore, exact_comparison
from .master import OpenAIMaster
from .models import (
    ConfirmRequest,
    EditRequest,
    RequestedProduct,
    SearchRequest,
    SessionCreated,
    SessionView,
    TurnRequest,
    VersionRequest,
)
from .providers import ProviderWorkers
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.commerce_mode != "live":
        raise ValueError("Only live commerce is supported. Set COMMERCE_MODE=live.")
    store, workers, master = SessionStore(), ProviderWorkers(settings), OpenAIMaster(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if master.client:
            await master.client.close()

    app = FastAPI(title="EasyDial", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.workers, app.state.master = store, workers, master

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(OpenAIError)
    async def openai_error(request, exc):
        return JSONResponse(
            status_code=502,
            content={
                "detail": "The voice service couldn't complete that request. Your basket is unchanged; please try again."
            },
        )

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 9 * 1024 * 1024):
            return JSONResponse(status_code=413, content={"detail": "Please record a shorter message."})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def session(session_id: str, authorization: Annotated[str, Header()] = "") -> Session:
        token = authorization.removeprefix("Bearer ")
        return store.get(session_id, token)

    SessionDep = Annotated[Session, Depends(session)]

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": settings.commerce_mode, "live_checkout": False}

    @app.get("/config")
    async def config():
        return {
            "mode": settings.commerce_mode,
            "voice_available": bool(settings.openai_api_key),
            "conversation_available": bool(settings.openai_api_key),
            "live_checkout": False,
            "providers": workers.connection_status(),
            "address_configured": bool(settings.live_address_label),
        }

    @app.post("/sessions", response_model=SessionCreated)
    async def new_session():
        address = settings.live_address_label
        created = store.create(settings.commerce_mode, address)
        return SessionCreated(session=created.view, token=created.token)

    @app.get("/sessions/{session_id}", response_model=SessionView)
    async def get_session(current: SessionDep):
        return current.view

    async def search_results(queries: list[RequestedProduct], address: str):
        if not queries or len(queries) > 3 or any(not q.query.strip() for q in queries):
            raise DomainError("Please name a product to search for.", 422)
        if not address:
            raise DomainError(
                "Connect a store and select your delivery address first. See Connection settings.", 503
            )
        batches = await asyncio.gather(*(workers.search(q.query.strip(), address) for q in queries))
        results = []
        for query, batch in zip(queries, batches):
            for result in batch:
                for offer in result.offers:
                    offer.suggested_quantity = query.quantity
                    offer.replaces_line_id = query.replaces_line_id
                results.append(result)
        return results

    def save_results(current, results):
        current.view.results = results
        for result in results:
            for offer in result.offers:
                current.offers[offer.id] = offer
        if len(current.offers) > 300:
            current.offers = {o.id: o for r in results for o in r.offers}
        offers = [o for result in results for o in result.offers]
        current.view.comparison = exact_comparison(offers)
        current.changed()
        if offers:
            current.say(
                current.view.comparison
                or "Here are the matching products. Choose the brand and pack size you want; fees are not included."
            )
        elif all(r.status != "ok" for r in results):
            current.say("I couldn't check either store. Please try again; availability is unknown.")
        else:
            current.say(
                "No matching products were returned by the available searches. Try a brand or a simpler product name."
            )

    @app.post("/sessions/{session_id}/search", response_model=SessionView)
    async def search(body: SearchRequest, current: SessionDep):
        async with current.lock:
            current.check(body.expected_version)
        results = await search_results(
            [RequestedProduct(query=body.query, quantity=1, replaces_line_id=None)],
            current.view.address_label,
        )
        async with current.lock:
            current.check(body.expected_version)
            current.say(body.query, "user")
            save_results(current, results)
            return current.view

    @app.post("/sessions/{session_id}/basket", response_model=SessionView)
    async def edit(body: EditRequest, current: SessionDep):
        async with current.lock:
            current.edit(body)
            current.say("Your basket is updated. You can change it again before reviewing.")
            return current.view

    @app.post("/sessions/{session_id}/review", response_model=SessionView)
    async def review(body: VersionRequest, current: SessionDep):
        async with current.lock:
            current.check(body.expected_version)
            current.view.review = None
            current.view.phase = "shopping"
            if not current.view.lines and current.view.synced_provider:
                await workers.clear(current.view)
                current.view.synced_provider = None
                current.changed()
                current.say("Your store basket is now empty.")
            else:
                if current.view.lines:
                    current.view.synced_provider = current.view.lines[0].offer.provider
                reviewed = await workers.review(current.view)
                current.accept_review(body.expected_version, reviewed)
            return current.view

    @app.post("/sessions/{session_id}/confirm", response_model=SessionView)
    async def confirm(body: ConfirmRequest, current: SessionDep):
        async with current.lock:
            current.confirm(body.expected_version, body.review_id, body.confirmed)
            return current.view

    @app.post("/sessions/{session_id}/turn", response_model=SessionView)
    async def turn(body: TurnRequest, current: SessionDep):
        async with current.lock:
            current.check(body.expected_version)
            snapshot = current.view.model_copy(deep=True)
        intent = await master.interpret(body.text, snapshot)
        if any(
            q.replaces_line_id is not None and q.replaces_line_id not in {x.id for x in snapshot.lines}
            for q in intent.queries
        ):
            raise DomainError("Which basket item should I replace?", 422)
        results = (
            await search_results(intent.queries, snapshot.address_label)
            if intent.action == "search"
            else None
        )
        async with current.lock:
            current.check(body.expected_version)
            # All edits in a turn succeed together or none take effect.
            candidate = Session(view=current.view.model_copy(deep=True), offers=dict(current.offers))
            candidate.say(body.text, "user")
            if intent.action == "search":
                save_results(candidate, results)
                if intent.clarification:
                    candidate.say(intent.clarification)
            elif intent.action == "edit":
                if not intent.edits:
                    raise DomainError("I couldn't identify the basket change. Please try again.", 422)
                for change in intent.edits:
                    candidate.edit(
                        EditRequest(
                            kind=change.kind,
                            line_id=change.line_id,
                            quantity=change.quantity,
                            expected_version=candidate.view.version,
                        )
                    )
                candidate.say("Your basket is updated. Please check the quantities below.")
            elif intent.action == "review":
                candidate.changed()
                candidate.say(
                    "Open your basket and tap Review basket to sync it with the store and check the live bill."
                )
            elif intent.action == "stop":
                candidate.changed()
                candidate.say("I'll wait. No order has been placed.")
            else:
                candidate.changed()
                candidate.say(intent.clarification or "Which product and pack size would you like?")
            current.view, current.offers = candidate.view, candidate.offers
            return current.view

    @app.post("/sessions/{session_id}/transcribe")
    async def transcribe(current: SessionDep, audio: Annotated[UploadFile, File()]):
        try:
            client = master.require_client()
            if audio.content_type not in {
                "audio/mp4",
                "audio/m4a",
                "audio/x-m4a",
                "audio/wav",
                "audio/mpeg",
                "audio/webm",
            }:
                raise DomainError("Please send an M4A, WAV, MP3 or WebM recording.", 422)
            data = await audio.read(8 * 1024 * 1024 + 1)
            if not data or len(data) > 8 * 1024 * 1024:
                raise DomainError("Record a short message, up to 60 seconds.", 413)
            result = await client.audio.transcriptions.create(
                model=settings.openai_transcription_model,
                file=(audio.filename or "recording.m4a", data, audio.content_type),
                prompt="Grocery conversation in Indian languages. Preserve brands, quantities, negation and corrections. Amul, Mother Dairy, Britannia, atta, dal, doodh.",
            )
            return {"text": result.text}
        finally:
            await audio.close()

    @app.post("/sessions/{session_id}/speech")
    async def speech(current: SessionDep):
        client = master.require_client()
        text = next((m.text for m in reversed(current.view.messages) if m.role == "assistant"), "")
        result = await client.audio.speech.create(
            model=settings.openai_speech_model,
            voice=settings.openai_voice,
            input=text[:2000],
            response_format="mp3",
            instructions="Speak warmly and clearly. Use short natural pauses. This is a grocery assistant.",
        )
        return Response(content=result.content, media_type="audio/mpeg")

    return app


app = create_app()
