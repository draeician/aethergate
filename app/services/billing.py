from sqlmodel import select
from app.database import get_session
from app.models import User, RequestLog, LLMModel, APIKey
import uuid

async def process_transaction(
    user_id: uuid.UUID,
    api_key_id: uuid.UUID,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    prompt_text: str = None,
    completion_text: str = None
):
    """
    Calculates cost, deducts balance, and logs the request.
    """
    # 1. Get Session
    async for session in get_session():
        # 2. Get Model Pricing
        # We query by the "public ID" (e.g. 'qwen2.5:3b')
        statement = select(LLMModel).where(LLMModel.id == model_id)
        results = await session.exec(statement)
        model_data = results.first()
        
        p_in = model_data.price_in if model_data else 0.0
        p_out = model_data.price_out if model_data else 0.0
        
        # 3. Calculate Cost
        cost = (input_tokens * p_in) + (output_tokens * p_out)
        
        # 4. Get User & Deduct
        user_result = await session.exec(select(User).where(User.id == user_id))
        user = user_result.first()
        
        if user:
            user.balance -= cost
            session.add(user)
            
            # 5. Log It
            log = RequestLog(
                user_id=user_id,
                api_key_id=api_key_id,
                model_used=model_id,
                input_units=float(input_tokens),
                output_units=float(output_tokens),
                total_cost=cost,
                prompt_text=prompt_text,     
                completion_text=completion_text 
            )
            session.add(log)
            await session.commit()
            
            print(f"--> [BILLING] {user.username}: -${cost:.6f} | New Bal: ${user.balance:.4f}")
        else:
            print("!!! [BILLING] User not found.")
