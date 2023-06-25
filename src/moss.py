import os
import torch
import numpy as np
import argparse
from mp_utils import choices, format_example, gen_prompt, softmax, run_eval

from huggingface_hub import snapshot_download
from transformers import AutoConfig, AutoTokenizer, AutoModelForCausalLM
from accelerate import init_empty_weights, load_checkpoint_and_dispatch

def eval(model, tokenizer, subject, dev_df, test_df, num_few_shot, max_length, cot):
    choice_ids = [tokenizer.encode(choice)[-1] for choice in choices]
    cors = []
    all_conf = []
    all_preds = []
    answers = choices[: test_df.shape[1] - 2]

    for i in range(test_df.shape[0]):
        prompt_end = format_example(test_df, i, subject, include_answer=False)
        prompt = gen_prompt(dev_df=dev_df,
                            subject=subject,
                            prompt_end=prompt_end,
                            num_few_shot=num_few_shot,
                            tokenizer=tokenizer,
                            max_length=max_length)
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        label = test_df.iloc[i, test_df.shape[1] - 1]

        with torch.no_grad():
            outputs = model(**inputs)
            last_token_logits = outputs.logits[:, -1, :]
            choice_logits = last_token_logits[:, choice_ids].detach().cpu().numpy()
            conf = softmax(choice_logits[0])[choices.index(label)]
            pred = {0: "A", 1: "B", 2: "C", 3: "D"}[np.argmax(choice_logits[0])]

        all_preds += pred
        all_conf.append(conf)
        cors.append(pred == label)

    acc = np.mean(cors)
    print("Average accuracy {:.3f} - {}".format(acc, subject))
    return acc, all_preds, all_conf


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name_or_path", type=str, default="")
    parser.add_argument("--lora_weights", type=str, default="")
    parser.add_argument("--data_dir", type=str, default="../data")
    parser.add_argument("--save_dir", type=str, default="../results/not_specified")
    parser.add_argument("--num_few_shot", type=int, default=0)
    parser.add_argument("--max_length", type=int, default=2048)
    parser.add_argument("--load_in_8bit", action='store_true')
    args = parser.parse_args()

    # Initialize models
    # Verified support models: llama, bloom, bactrian,
    hf_home = os.environ.get('HF_HOME')
    model_path = os.path.join(hf_home, args.model_name_or_path)
    if not os.path.exists(model_path):
        model_path = snapshot_download(args.model_name_or_path)
    config = AutoConfig.from_pretrained(args.model_name_or_path, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name_or_path, trust_remote_code=True)
    with init_empty_weights():
        model = AutoModelForCausalLM.from_config(config, torch_dtype=torch.float16, trust_remote_code=True)
    model.tie_weights()
    model = load_checkpoint_and_dispatch(model, model_path, device_map="auto", no_split_module_classes=["MossBlock"], dtype=torch.float16)

    run_eval(model, tokenizer, eval, args)