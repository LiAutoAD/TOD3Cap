import os
import json
from pathlib import Path

import clip
import torch
import torch.nn as nn
from timm.models.vision_transformer import Block

from .llama import ModelArgs, Transformer
from .tokenizer import Tokenizer
from .utils import sample_top_p, _download
import os
import json
from pathlib import Path
import copy, math
import clip
import torch
import torch.nn as nn
from torch import nn, Tensor
import torch.nn.functional as nnf
from typing import Dict
from collections import OrderedDict
from transformers import GPT2Config, GPT2LMHeadModel
from transformers import GPT2Tokenizer

from .tokenizer import Tokenizer as LLAMATokenizer

import numpy as np

# def token_llama2gpt2(labels, max_des_len=64):

#     device = labels.device
#     labels = labels.detach().cpu().numpy()

#     llama_tokenizer = LLAMATokenizer(model_path="/data18/jinbu/nuscenes-caption/Attribute/LLaMA-Adapter//LLaMA-7B/tokenizer.model")
#     gpt2_tokenizer = GPT2Tokenizer.from_pretrained('gpt2')

#     begin_id = 14

#     reference_tokens = np.zeros((2, max_des_len))
#     reference_masks  = np.zeros((2, max_des_len))

#     raw_sentences = []
#     for label_id, label in enumerate(labels):
#         label = label.tolist()
#         end_id = label.index(llama_tokenizer.eos_id)
#         my_label = label[42:end_id]
#         sentence = llama_tokenizer.decode(my_label)
#         raw_sentences.append(sentence)
#     tokenized_captions = gpt2_tokenizer.batch_encode_plus(raw_sentences)
#     input_ids = tokenized_captions['input_ids']
#     attention_masks = tokenized_captions['attention_mask']

#     for label_id, (input_id, attention_mask) in enumerate(zip(input_ids, attention_masks)):
#         sentence_length = len(input_id)
#         reference_tokens[label_id, :sentence_length] = input_id
#         reference_masks[label_id, :sentence_length] = attention_mask

#     return torch.tensor(reference_tokens.astype(np.int64)).to(device), torch.tensor(reference_masks.astype(np.float32)).to(device)

def position_embedding(max_len: int, d_model: int) -> Tensor:
    position_embedding = torch.zeros(max_len, d_model)
    position = torch.arange(0, max_len).unsqueeze(1).float()
    div_term = torch.exp(torch.arange(0, d_model, 2).float() *
                         -(math.log(10000.0) / d_model))
    position_embedding[:, 0::2] = torch.sin(position * div_term)
    position_embedding[:, 1::2] = torch.cos(position * div_term)
    return position_embedding

class LLaMA_adapter(nn.Module):

    def __init__(self, llama_ckpt_dir, llama_tokenizer,
                 max_seq_len=512, max_batch_size=1,
                 clip_model='ViT-L/14',
                 v_embed_dim=768, v_depth=8,
                 v_num_heads=16, v_mlp_ratio=4.0,
                 query_len=10, query_layer=31,
                 w_bias=False, 
                 w_lora=False, lora_rank=16, 
                 w_new_gate=False,
                 phase="finetune"):
        super().__init__()

        # load llama configs
        with open(os.path.join(llama_ckpt_dir, "params.json"), "r") as f:
            params = json.loads(f.read())
        w_bias = phase == "finetune"
        model_args: ModelArgs = ModelArgs(
            max_seq_len=max_seq_len, max_batch_size=max_batch_size, **params
        ) # max_batch_size only affects inferenc

        # 1. bev projector
        self.bev_dim = 256
        self.downsample = nn.Sequential(
            nn.Conv3d(in_channels=self.bev_dim, out_channels=self.bev_dim, kernel_size=1, stride=5, bias=False),
            nn.BatchNorm3d(self.bev_dim), nn.ReLU())

        self.clip, self.clip_transform = clip.load(clip_model)
        clip_dim = self.clip.visual.proj.shape[1]
        self.bev_proj = nn.Linear(self.bev_dim, v_embed_dim)
        self.bev_proj_norm = nn.LayerNorm(v_embed_dim)

        self.query_len = query_len
        self.query_layer = query_layer



        # 2. visual query, blocks and projector
        self.bbox_len = 10
        self.bbox_query = nn.Linear(self.bbox_len, v_embed_dim)
        self.visual_blocks = nn.ModuleList([
            Block(v_embed_dim, v_num_heads, v_mlp_ratio, qkv_bias=True)
            for _ in range(v_depth)])
        self.visual_proj = nn.Linear(v_embed_dim, 256)
        self.visual_proj_norm = nn.LayerNorm(256)

        # # 3. adapter query
        # self.adapter_query = nn.Embedding(
        #     query_len * query_layer, model_args.dim)



        # 4. tokenizer
        self.tokenizer = GPT2Tokenizer.from_pretrained('gpt2')
        self.nvocabs = len(self.tokenizer)

        self.embedding_size = 256
        self.max_positions = 128
        self.max_des_len = 64
        gpt2_config = GPT2Config(
            vocab_size=self.nvocabs,
            n_positions=self.max_positions,
            n_embd=self.embedding_size,
            n_layer=2,
            n_head=4,
            bos_token_id=self.tokenizer.bos_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
            add_cross_attention=True,
        )

        self.transformer = GPT2LMHeadModel(config=gpt2_config)
        self.transformer.transformer.wpe = nn.Embedding.from_pretrained(
            position_embedding(self.max_positions, self.embedding_size)
        )

        # ## for proposal feature projection
        # self.feature_projector = nn.Sequential(
        #     nn.Linear(256, self.embedding_size),
        #     nn.LayerNorm(self.embedding_size),
        #     nn.ReLU(),
        #     nn.Linear(self.embedding_size, self.embedding_size),
        # )
        
        # self.context_projector = nn.Sequential(
        #     nn.Linear(256, self.embedding_size),
        #     nn.ReLU(),
        #     nn.Linear(self.embedding_size, self.embedding_size),
        #     nn.ReLU(),
        # )

        # self.tokenizer = Tokenizer(model_path=llama_tokenizer)
        # # 5. llama
        # model_args.w_bias = w_bias
        # model_args.w_lora = w_lora
        # model_args.lora_rank = lora_rank
        # model_args.w_new_gate = w_new_gate
        # model_args.vocab_size = self.tokenizer.n_words
        # torch.set_default_tensor_type(torch.cuda.HalfTensor)
        # self.llama = Transformer(model_args)
        # torch.set_default_tensor_type(torch.FloatTensor)

        # ckpts = sorted(Path(llama_ckpt_dir).glob("*.pth"))
        # for ckpt in ckpts:
        #     ckpt = torch.load(ckpt, map_location='cpu')
        #     self.llama.load_state_dict(ckpt, strict=False)

        # # del self.clip.transformer

        #  # 6. training criterion
        # self.criterion = torch.nn.CrossEntropyLoss(ignore_index=0)

        # # 7. training parameters
        # self.phase = phase
        # self.get_trainable_params(self.phase)

        names = []
        for name, param in self.named_parameters():
            names.append(name)
            if param.requires_grad:
               print(f"Trainable param: {name}, {param.shape}, {param.dtype}")
        print(names)

    def get_trainable_params(self, phase='finetune'):
        for name, para in self.named_parameters():
            para.requires_grad = False

        if phase == 'finetune':
            for name, para in self.named_parameters():
                if name.startswith("llama."):
                    if 'norm' in name or 'bias' in name:
                        para.data = para.data.float()
                        para.requires_grad = True

        elif phase == 'pretrain':
            train_param_name = ['gate', 'bev_proj', 'bev_proj_norm', 'bbox_query', 'visual_blocks', 'visual_proj', 'visual_proj_norm', 'adapter_query']
            for name, para in self.named_parameters():
                for train_name in train_param_name:
                    if train_name in name:
                        para.data = para.data.float()
                        para.requires_grad = True
        
        else:
            raise ValueError(f"Unknown model phase: {phase}")
        
    # def clip_encode_image(self, x):
    #     # modified from CLIP
    #     x = self.clip.visual.conv1(x)  # shape = [*, width, grid, grid]
    #     # shape = [*, width, grid ** 2]
    #     x = x.reshape(x.shape[0], x.shape[1], -1)
    #     x = x.permute(0, 2, 1)  # shape = [*, grid ** 2, width]
    #     x = torch.cat([self.clip.visual.class_embedding.to(x.dtype) + torch.zeros(x.shape[0], 1,
    #                   x.shape[-1], dtype=x.dtype, device=x.device), x], dim=1)  # shape = [*, grid ** 2 + 1, width]
    #     x = x + self.clip.visual.positional_embedding.to(x.dtype)
    #     x = self.clip.visual.ln_pre(x)

    #     x = x.permute(1, 0, 2)  # NLD -> LND
    #     x = self.clip.visual.transformer(x)
    #     x = x.permute(1, 0, 2)  # LND -> NLD

    #     # preserve all spatial tokens
    #     x = self.clip.visual.ln_post(x[:, :, :])

    #     if self.clip.visual.proj is not None:
    #         x = x @ self.clip.visual.proj

    #     return x


    def forward_visual(self, det_inputs):
        feats, pred = det_inputs

        feats = feats.permute(0, 2, 1) # B 256 2500
        
        bev_size = int(feats.size(-1)**0.5)
        
        feats = feats.contiguous().view(feats.size(0), self.bev_dim, 1, bev_size, bev_size)
        feats = self.downsample(feats)
        # feats = feats.contiguous().view(len(feats), self.bev_dim, -1)
        feats = feats.view(len(feats), self.bev_dim, -1)
        feats = feats.permute(0, 2, 1)    # B bev_size/5*bev_size/5 256

        clip_feats = self.bev_proj_norm(self.bev_proj(feats.float()))

        bbox_query = self.bbox_query(pred.unsqueeze(-2))
        bbox_query = bbox_query    # B 1 768

        bbox_query = torch.cat([bbox_query, clip_feats], dim=1)
        for block in self.visual_blocks:
            bbox_query = block(bbox_query)

        bbox_query = bbox_query[:, :self.query_len, :]
        bbox_query = self.visual_proj(bbox_query)
        bbox_query = self.visual_proj_norm(bbox_query)

        return bbox_query

    def forward(self, cap_inputs, det_inputs):
        tokens, labels, c_weights = cap_inputs
        feats, obj_pred_box = det_inputs

        bbox_query = self.forward_visual(det_inputs)

        gt_box_cap_label, gt_box_cap_masks = tokens, labels
        text_embeddings = self.transformer.transformer.wte(gt_box_cap_label)

        inputs_embeds = torch.cat([
            bbox_query, text_embeddings
        ], dim=1)   # batch x nproposals x (nprefix + max_des_len) x channel

        inputs_masks = torch.cat([
            torch.ones_like(bbox_query[..., 0]), gt_box_cap_masks
        ], dim=1)   # batch x nproposals x (nprefix + max_des_len)

        outputs = self.transformer( # num_annotated x (1 + max_des_len)
            inputs_embeds=inputs_embeds,
            attention_mask=inputs_masks,
            encoder_hidden_states=None
        )

        c_loss = self.loss_caption(
            logits = outputs.logits[:, bbox_query.shape[1] - 1: -1],
            target = gt_box_cap_label.long()
        )

        print(c_loss)

        return c_loss

    def loss_caption(self, logits: Tensor, target: Tensor) -> Tensor:
        loss_config = {'reduction': 'none', 'ignore_index': 0}
        
        loss_per_word = nnf.cross_entropy(
            logits.reshape(-1, self.nvocabs),
            target.reshape(-1), 
            **loss_config
        )
        loss_per_word = loss_per_word.reshape(target.shape)
        final_loss = torch.sum(loss_per_word * (target != 0).float()) / torch.sum(
            torch.sum(target != 0).float() + 1e-6
        )
        return final_loss

    @torch.inference_mode()
    def forward_inference(self, bbox_query, tokens, start_pos: int):
        _bsz, seqlen = tokens.shape
        h = self.llama.tok_embeddings(tokens)
        freqs_cis = self.llama.freqs_cis.to(h.device)
        freqs_cis = freqs_cis[start_pos : start_pos + seqlen]
        mask = None
        mask = torch.full((1, 1, seqlen, seqlen), float("-inf"), device=h.device)
        mask = torch.triu(mask, diagonal=start_pos + 1).type_as(h)

        for layer in self.llama.layers[:-1 * self.query_layer]:
            h = layer(h, start_pos, freqs_cis, mask)

        adapter = self.adapter_query.weight.reshape(self.query_layer, self.query_len, -1).unsqueeze(1)
        adapter_index = 0
        for layer in self.llama.layers[-1 * self.query_layer:]:
            dynamic_adapter = adapter[adapter_index].repeat(_bsz, 1, 1)
            dynamic_adapter = dynamic_adapter + bbox_query
            h = layer(h, start_pos, freqs_cis, mask, dynamic_adapter)
            adapter_index = adapter_index + 1

        h = self.llama.norm(h)
        output = self.llama.output(h[:, -1, :])

        return output.float()

    @torch.inference_mode()
    def generate(
        self, det_inputs, cap_inputs,
        max_gen_len: int = 256,
        temperature: float = 0.1,
        top_p: float = 0.75,
    ):
        prompts = cap_inputs

        bsz = len(det_inputs[0])
        params = self.llama.params
        assert bsz <= params.max_batch_size, (bsz, params.max_batch_size)
        assert len(det_inputs[0]) == len(prompts)

        with torch.cuda.amp.autocast():
            bbox_query = self.forward_visual(det_inputs)

        if isinstance(prompts[0], str):
            prompts = [self.tokenizer.encode(x, bos=True, eos=False) for x in prompts]

        min_prompt_size = min([len(t) for t in prompts])
        max_prompt_size = max([len(t) for t in prompts])

        total_len = min(params.max_seq_len, max_gen_len + max_prompt_size)

        tokens = torch.full((bsz, total_len), self.tokenizer.pad_id).cuda().long()

        for k, t in enumerate(prompts):
            tokens[k, : len(t)] = torch.tensor(t).cuda().long()
        input_text_mask = tokens != self.tokenizer.pad_id
        start_pos = min_prompt_size
        prev_pos = 0
        for cur_pos in range(start_pos, total_len):
            with torch.cuda.amp.autocast():
                logits = self.forward_inference(bbox_query, tokens[:, prev_pos:cur_pos], prev_pos)
            if temperature > 0:
                probs = torch.softmax(logits / temperature, dim=-1)
                next_token = sample_top_p(probs, top_p)
            else:
                next_token = torch.argmax(logits, dim=-1)
            next_token = next_token.reshape(-1)

            next_token = torch.where(
                input_text_mask[:, cur_pos], tokens[:, cur_pos], next_token
            )
            tokens[:, cur_pos] = next_token
            # trick: early stop if bsz==1
            if bsz == 1 and next_token[0] == self.tokenizer.eos_id:
                break
            prev_pos = cur_pos

        decoded = []
        for i, t in enumerate(tokens.tolist()):

            # cut to max gen len
            t = t[len(prompts[i]): len(prompts[i]) + max_gen_len]
            # cut to eos tok if any
            try:
                t = t[: t.index(self.tokenizer.eos_id)]
            except ValueError:
                pass
            decoded.append(self.tokenizer.decode(t))

        return decoded


_MODELS = {
    "BIAS-7B": "https://github.com/OpenGVLab/LLaMA-Adapter/releases/download/v.2.0.0/7fa55208379faf2dd862565284101b0e4a2a72114d6490a95e432cf9d9b6c813_BIAS-7B.pth",
    "LORA-BIAS-7B": "https://github.com/OpenGVLab/LLaMA-Adapter/releases/download/v.2.0.0/1bcbffc43484332672092e0024a8699a6eb5f558161aebf98a7c6b1db67224d1_LORA-BIAS-7B.pth",
    # "LORA16-7B": "",
    # "PARTIAL-7B": ""
}

def available_models():
    return list(_MODELS.keys())

def load(name, llama_dir, device="cuda" if torch.cuda.is_available() else "cpu", download_root='ckpts', max_seq_len=512,
        phase="finetune"):
    if name in _MODELS:
        model_path = _download(_MODELS[name], download_root)
    elif os.path.isfile(name):
        model_path = name
    else:
        return RuntimeError(f"Model {name} not found; available models = {available_models()}"), None

    # BIAS-7B or https://xxx/sha256_BIAS-7B.pth -> 7B
    llama_type = name.split('.')[0].split('-')[-1]
    llama_ckpt_dir = os.path.join(llama_dir, llama_type)
    llama_tokenzier_path = os.path.join(llama_dir, 'tokenizer.model')

    # load llama_adapter weights and model_cfg
    print(f'Loading LLaMA-Adapter from {model_path}')
    ckpt = torch.load(model_path, map_location='cpu')
    model_cfg = ckpt.get('config', {})

    model = LLaMA_adapter(
        llama_ckpt_dir, llama_tokenzier_path,
        max_seq_len=512, max_batch_size=1,
        clip_model='ViT-L/14',
        v_embed_dim=768, v_depth=8,
        v_num_heads=16, v_mlp_ratio=4.0,
        query_len=10, query_layer=31,
        w_bias=model_cfg.get('w_bias', False), 
        w_lora=model_cfg.get('w_lora', False), 
        lora_rank=model_cfg.get('lora_rank', 16),
        w_new_gate=model_cfg.get('w_lora', False), # for compatibility
        phase=phase)

    load_result = model.load_state_dict(ckpt['model'], strict=False)

    assert len(load_result.unexpected_keys) == 0, f"Unexpected keys: {load_result.unexpected_keys}"
    return model.to(device), model.clip_transform
