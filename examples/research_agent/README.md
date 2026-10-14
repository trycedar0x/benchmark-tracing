# Research assistant

A short LangGraph tool loop. No API key. The model is local: it reads the instructions, the tool node runs the tool it asked for, then it answers from that result.

The instructions tell it to use only `read_headline`. `read_recent_history` is a real tool on the same graph. Because the instructions never name it, the assistant answers from the headline alone.

The page at `/try` asks the six practice questions and shows the score in plain language. That page does not mention this framework.
