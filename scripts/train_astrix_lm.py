"""Fine-tune a generative Astrix-LM from the verified-decision corpus.

The nano model (backend/app/training/nano.py) learns *labels*. This script
teaches a small open LLM to produce the full structured decision — diagnosis,
risk and recovery — in the same JSON the agents emit, using LoRA so it trains on
a single consumer GPU (or a free Colab/Kaggle T4).

    pip install -r requirements-train.txt

    # 1. get data: console → Astrix-LM → "Export JSONL", or directly from the corpus
    python scripts/train_astrix_lm.py --from-corpus --out training/astrix-lm
    # or
    python scripts/train_astrix_lm.py --data astrix-lm-sft.jsonl --out training/astrix-lm

    # 2. serve it (see training/Modelfile)

Continuous training: re-run with `--resume training/astrix-lm/adapter` as the
corpus grows; each run writes `run.json` recording the corpus chain head and
example count it saw, so every model version is traceable to its data.

The fine-tuned model stays advisory. Whatever it outputs still passes through
the deterministic safety engine and digital twin before any action executes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_records(args) -> tuple[list[dict], str | None]:
    if args.from_corpus:
        from backend.app.config import get_settings
        from backend.app.training.corpus import TrainingCorpus

        s = get_settings()
        corpus = TrainingCorpus(s.corpus_path, s.corpus_key)
        integrity = corpus.verify()
        if not integrity["ok"]:
            raise SystemExit(f"corpus failed integrity check: {integrity}")
        return corpus.export_sft(only_verified=not args.include_unverified), integrity.get("chain_head")
    lines = Path(args.data).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()], None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LoRA fine-tune Astrix-LM")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--data", help="chat-format JSONL exported from /model/export")
    source.add_argument("--from-corpus", action="store_true", help="read the encrypted corpus directly")
    parser.add_argument("--include-unverified", action="store_true")
    parser.add_argument("--base", default="Qwen/Qwen2.5-0.5B-Instruct", help="Hugging Face base model")
    parser.add_argument("--out", default="training/astrix-lm")
    parser.add_argument("--resume", help="existing LoRA adapter directory to continue training")
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--max-len", type=int, default=1024)
    parser.add_argument("--no-merge", action="store_true", help="skip writing a merged full model")
    args = parser.parse_args(argv)

    records, chain_head = load_records(args)
    if len(records) < 20:
        print(f"only {len(records)} examples — collect more by running missions and injecting faults.")
        if len(records) < 4:
            return 1

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, PeftModel, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError:
        print("missing training dependencies: pip install -r requirements-train.txt", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    random.Random(7).shuffle(records)
    split = max(1, int(len(records) * 0.1))
    eval_records, train_records = records[:split], records[split:]

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def encode(example):
        text = tokenizer.apply_chat_template(example["messages"], tokenize=False)
        tokens = tokenizer(text, truncation=True, max_length=args.max_len)
        tokens["labels"] = tokens["input_ids"].copy()
        return tokens

    train_ds = Dataset.from_list(train_records).map(encode, remove_columns=["messages"])
    eval_ds = Dataset.from_list(eval_records).map(encode, remove_columns=["messages"])

    use_cuda = torch.cuda.is_available()
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.bfloat16 if use_cuda else torch.float32
    )
    if args.resume:
        model = PeftModel.from_pretrained(model, args.resume, is_trainable=True)
    else:
        model = get_peft_model(
            model,
            LoraConfig(
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                task_type="CAUSAL_LM",
            ),
        )
    model.print_trainable_parameters()

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            per_device_train_batch_size=args.batch,
            per_device_eval_batch_size=args.batch,
            gradient_accumulation_steps=2,
            eval_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=1,
            logging_steps=10,
            bf16=use_cuda,
            report_to=[],
        ),
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )
    trainer.train()
    metrics = trainer.evaluate()

    adapter_dir = out / "adapter"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    if not args.no_merge:
        merged = model.merge_and_unload()
        merged.save_pretrained(out / "merged")
        tokenizer.save_pretrained(out / "merged")

    run = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "base_model": args.base,
        "resumed_from": args.resume,
        "train_examples": len(train_records),
        "eval_examples": len(eval_records),
        "corpus_chain_head": chain_head,
        "eval_loss": metrics.get("eval_loss"),
    }
    (out / "run.json").write_text(json.dumps(run, indent=2))
    print(json.dumps(run, indent=2))
    print(f"\nadapter: {adapter_dir}\nnext: convert to GGUF and `ollama create astrix-lm -f training/Modelfile`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
