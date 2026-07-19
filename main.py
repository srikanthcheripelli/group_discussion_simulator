from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import os
from dotenv import load_dotenv
import base64
import asyncio
import edge_tts

GRADE_PROMPTS = {
    "grade_1_3": "Speak very simply, like you are 7 years old. Use short sentences. Be friendly.",
    "grade_4_6": "Speak clearly like you are 10 years old. Use simple words and 1-2 ideas per turn.",
    "grade_7_9": "Speak like a middle schooler. Give 1 example and ask a follow-up question sometimes.",
    "grade_10_12": "Speak like a high schooler. Give opinions, reasons, and examples. Be thoughtful.",
    "grade_11_plus": "Speak like a college student. Be articulate, give analysis and different viewpoints."
}

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)

CHILD_VOICE_LEVELS = {"grade_1_3", "grade_4_6"}

ADULT_VOICES = {
    "avatar1": "en-US-AriaNeural",
    "avatar2": "en-US-GuyNeural",
    "user": "en-US-DavisNeural",
}

CHILD_VOICES = {
    "avatar1": "en-US-AriaNeural",
    "avatar2": "en-US-EricNeural",
    "user": "en-US-DavisNeural",
}

discussion = {
    "topic": "",
    "level": "grade_11_plus",
    "max_rounds": 5,
    "max_words": 60,
    "turn_order": ["avatar1", "avatar2", "user"], # KEYWORD 'user'
    "names": {"avatar1": "Avatar 1", "avatar2": "Avatar 2", "user": "You"},
    "current_turn_index": 0,
    "history": [],
}

def get_voice_map(level):
    if level in CHILD_VOICE_LEVELS:
        return CHILD_VOICES
    return ADULT_VOICES

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start", methods=["POST"])
def start_discussion():
    global discussion
    
    data = request.json
    topic = data['topic']
    level = data['level']
    name1 = data['name1'] 
    name2 = data['name2'] 
    name3 = data['name3'] 
    
    # SET MAX ROUNDS BASED ON GRADE
    # SET MAX ROUNDS - ALWAYS 5
    max_rounds = 5
    max_words = 60
    
    print(f"=== NEW DISCUSSION: {topic} | Level: {level} | Rounds: {max_rounds} ===")
    
    discussion = {
        "topic": topic,
        "level": level,
        "max_rounds": max_rounds,
        "max_words": max_words,
        "names": {
            "avatar1": name1,
            "avatar2": name2,
            "user": name3
        },
        "history": [{"role": "system", "content": f"Discussion about {topic}"}],
        "turn_order": ['avatar1', 'avatar2', 'user'], # 'user' is the keyword to wait
        "current_turn_index": 0
    }
    
    return run_ai_turn() # start with avatar1


def run_ai_turn():
    global discussion
    
    turn_index = discussion["current_turn_index"]
    max_turns = discussion["max_rounds"] * 3
    current_round = (turn_index // 3) + 1

    speaker_key = discussion["turn_order"][turn_index % 3] # avatar1, avatar2, or 'user'
    next_speaker_key = discussion["turn_order"][(turn_index + 1) % 3]
    speaker_name = discussion["names"][speaker_key]
    next_speaker_name = discussion["names"][next_speaker_key]
    
    # CHECK 2: IF IT'S USER TURN, DON'T GENERATE AI. JUST WAIT
    if speaker_key == 'user':
                return jsonify({
                    "round": current_round,
                    "max_rounds": discussion["max_rounds"],  # <-- ADD THIS LINE HERE
                    "speaker": speaker_key, # send 'user'
                    "speaker_name": speaker_name, # send 'You'
                    "message": "", # empty so frontend waits
                    "next_speaker": next_speaker_key
                })
    
    # CHECK 1: STOP AFTER MAX ROUNDS
    if turn_index >= max_turns:
        return jsonify({
            "round": "DONE", 
            "max_rounds": discussion["max_rounds"],  # <-- ADD THIS LINE HERE
            "message": "Discussion complete! Great job!"
        })    

    # ONLY AVATARS COME HERE
    history_lines = [msg["content"] for msg in discussion["history"][1:]]
    last_messages = "\n".join(history_lines[-6:])

    grade_instruction = GRADE_PROMPTS.get(discussion['level'], "")

    prompt = f"""You are {speaker_name}. Topic: {discussion['topic']}.
{grade_instruction}
Max {discussion['max_words']} words. Say ONLY 1-2 short sentences. Then stop.
History: {last_messages}
Your turn:"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=80
    )
    message = response.choices[0].message.content.strip()
    discussion["history"].append({"role": "assistant", "content": message})
    return jsonify({
        "round": current_round,
        "max_rounds": discussion["max_rounds"],  # <-- ADD THIS LINE HERE
        "speaker": speaker_key, # send 'avatar1'
        "speaker_name": speaker_name, # send 'Tanishka'
        "message": message,
        "next_speaker": next_speaker_key
    })


@app.route("/chat", methods=["POST"])
def chat():
    global discussion
    
    user_message = request.json.get("message", "").strip()
    speaker_key = discussion["turn_order"][discussion["current_turn_index"] % 3]
    
    # THIS ROUTE ONLY RUNS WHEN USER CLICKS STOP
    if speaker_key == 'user' and user_message:
        user_name = discussion["names"]["user"]
        discussion["history"].append({"role": "user", "content": f"{user_name}: {user_message}"})
        discussion["current_turn_index"] += 1 # NOW advance to next person
        return run_ai_turn() # generate next avatar
    
    return jsonify({"error": "Not user's turn"})


@app.route("/tts", methods=["POST"])
def tts():
    data = request.json
    text = data.get('text', '')
    speaker_key = data.get('speaker', '')
    level = data.get("level") or discussion.get("level", "grade_11_plus")
    voice_map = get_voice_map(level)
    voice = voice_map.get(speaker_key, 'en-US-JennyNeural')

    async def generate():
        communicate = edge_tts.Communicate(text, voice)
        audio_data = b''
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    try:
        audio_bytes = asyncio.run(generate())
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        return jsonify({"audio": audio_b64})
    except Exception as e:
        print(f"TTS error: {e}")
        return jsonify({"audio": None})


@app.route("/next_turn", methods=["GET"])
def next_turn():
    global discussion
    
    # FIX: ONLY ADVANCE 1 PERSON. NO LOOP
    discussion["current_turn_index"] += 1
    return run_ai_turn()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", debug=True, port=port)