import config
from ext import pickle_save, pickle_load

from torch import tensor, Tensor, cat, stack
from torch import zeros, ones, eye, randn
from torch import sigmoid, tanh, relu, softmax
from torch import pow, log, sqrt, norm
from torch import float32, no_grad
from torch.nn.init import xavier_normal_

from collections import namedtuple

##


FF = namedtuple('FF', 'w')
LSTM = namedtuple('LSTM', 'wf bf wk bk wi bi ws bs')


def make_Llayer(in_size, layer_size):

    layer = LSTM(
        randn(in_size+layer_size, layer_size, requires_grad=True, dtype=float32),
        zeros(1,                  layer_size, requires_grad=True, dtype=float32),
        randn(in_size+layer_size, layer_size, requires_grad=True, dtype=float32),
        zeros(1,                  layer_size, requires_grad=True, dtype=float32),
        randn(in_size+layer_size, layer_size, requires_grad=True, dtype=float32),
        zeros(1,                  layer_size, requires_grad=True, dtype=float32),
        randn(in_size+layer_size, layer_size, requires_grad=True, dtype=float32),
        zeros(1,                  layer_size, requires_grad=True, dtype=float32),
    )

    with no_grad():
        for k,v in layer._asdict().items():
            if k == 'bf':
                v += config.forget_bias
        # layer.bf += config.forget_bias

    if config.init_xavier:
        xavier_normal_(layer.wf)
        xavier_normal_(layer.wk)
        xavier_normal_(layer.ws)
        xavier_normal_(layer.wi, gain=5/3)

    return layer

def make_Flayer(in_size, layer_size):

    layer = FF(
        randn(in_size, layer_size, requires_grad=True, dtype=float32),
        # zeros(1, layer_size,       requires_grad=True, dtype=float32),
    )

    return layer


make_layer = {
    'l': make_Llayer,
    'f': make_Flayer,
}


def prop_Llayer(layer, state, input):

    layer_size = layer.wf.size(1)
    prev_out = state[:,:layer_size]
    state = state[:,layer_size:]

    inp = cat([input,prev_out],dim=1)

    forget = sigmoid(inp@layer.wf + layer.bf)
    keep   = sigmoid(inp@layer.wk + layer.bk)
    interm = tanh   (inp@layer.wi + layer.bi)
    show   = sigmoid(inp@layer.ws + layer.bs)

    state = forget*state + keep*interm
    out = show*tanh(state)

    return out, cat([out,state],dim=1)

def prop_Llayer2(layer, state, input):

    inp = cat([input,state],dim=1)

    forget = sigmoid(inp@layer.wf + layer.bf)
    keep   = sigmoid(inp@layer.wk + layer.bk)
    interm = tanh   (inp@layer.wi + layer.bi)

    state  = forget*state + keep*interm
    inp = cat([input,state],dim=1)

    show   = sigmoid(inp@layer.ws + layer.bs)

    out = show*tanh(state)

    return out, state

def prop_Flayer(layer, inp):

    return tanh(inp@layer.w) # + layer.b)


prop_layer = {
    LSTM: prop_Llayer2,
    FF: prop_Flayer,
}


def make_model(info=None):

    if not info: info = config.creation_info

    layer_sizes = [e for e in info if type(e)==int]
    layer_types = [e for e in info if type(e)==str]

    return [make_layer[layer_type](layer_sizes[i], layer_sizes[i+1]) for i,layer_type in enumerate(layer_types)]


def prop_model(model, states, inp):
    new_states = []

    out = inp

    state_ctr = 0

    for layer in model:

        if type(layer) != FF:

            out, state = prop_layer[type(layer)](layer, states[state_ctr], out)
            new_states.append(state)
            state_ctr += 1

        else:

            out = prop_Flayer(layer, out)

        # dropout(out, inplace=True)

    return out, new_states


def respond_to(model, sequence, states=None):  # , wave_state=None):

    # TODO: fill out completely

    pass


def sequence_loss(label, output, do_stack=True, do_grad=True):

    if do_stack:
        label = stack(label,dim=0)
        output = stack(output,dim=0)

    if config.loss_squared:
        loss = pow(label-output,2).sum()
    else:
        loss = (label-output).abs().sum()

    if do_grad:
        loss.backward(retain_graph=True)

    return float(loss)


def sgd(model, lr=None, batch_size=None):

    if not lr: lr = config.learning_rate
    if not batch_size: batch_size = config.batch_size

    with no_grad():

        for layer in model:
            for param in layer._asdict().values():
                if param.requires_grad:

                    param.grad /=batch_size

                    if config.gradient_clip:
                        param.grad.clamp(min=-config.gradient_clip,max=config.gradient_clip)

                    param -= lr * param.grad
                    param.grad = None


moments, variances = [], []

def adaptive_sgd(model, epoch_nr, lr=None, batch_size=None,
                 alpha_moment=0.9,alpha_variance=0.999,epsilon=1e-8,
                 grad_scaling=False):

    if not lr: lr = config.learning_rate
    if not batch_size: batch_size = config.batch_size

    global moments, variances

    if not (moments and variances):
        for layer in model:
            moments.append([zeros(weight.size()) for weight in layer._asdict().values()])
            variances.append([zeros(weight.size()) for weight in layer._asdict().values()])

    with no_grad():

        for _, layer in enumerate(model):
            for __, weight in enumerate(getattr(layer,field) for field in layer._fields):
                if weight.requires_grad:

                    lr_ = lr

                    weight.grad /= batch_size

                    if moments:
                        moments[_][__] = alpha_moment * moments[_][__] + (1-alpha_moment) * weight.grad
                        moment_hat = moments[_][__] / (1-alpha_moment**(epoch_nr+1))
                    if variances:
                        variances[_][__] = alpha_variance * variances[_][__] + (1-alpha_variance) * weight.grad**2
                        variance_hat = variances[_][__] / (1-alpha_variance**(epoch_nr+1))
                    if grad_scaling:
                        lr_ *= norm(weight)/norm(weight.grad)

                    weight -= lr_ * (moment_hat if moments else weight.grad) / ((sqrt(variance_hat)+epsilon) if variances else 1)

                    weight.grad = None


def empty_states(model, batch_size=1):
    states = []
    for layer in model:
        if type(layer) != FF:
            state = zeros(batch_size, getattr(layer,layer._fields[0]).size(1))
            # if type(layer) == LSTM: # only for regular prop (prop2 is better.)
            #     state = cat([state]*2,dim=1)
            states.append(state)
    return states


def load_model(path=None, fresh_meta=None, py_serialize=True):
    if not path: path = config.model_path
    if not fresh_meta: fresh_meta = config.fresh_meta
    obj = pickle_load(path+'.pk')
    if obj:
        model, meta = obj
        if py_serialize:
            model = [type(layer)(*[tensor(getattr(layer,field),requires_grad=True) for field in layer._fields]) for layer in model]
        global moments, variances
        if fresh_meta:
            moments, variances = [], []
        else:
            moments, variances = meta
            if py_serialize:
                moments = [[tensor(e) for e in ee] for ee in moments]
                variances = [[tensor(e) for e in ee] for ee in variances]
        return model

def save_model(model, path=None, py_serialize=True):
    if not path: path = config.model_path
    if py_serialize:
        model = [type(layer)(*[getattr(layer,field).detach().numpy() for field in layer._fields]) for layer in model]
        meta = [[[e.detach().numpy() for e in ee] for ee in moments],[[e.detach().numpy() for e in ee] for ee in variances]]
    else:
        meta = [moments,variances]
    pickle_save([model,meta],path+'.pk')

def describe_model(model):
    return f'{config.in_size} ' + ' '.join(str(type(layer)) + " " + str(getattr(layer, layer._fields[0]).size(1)) for layer in model)

def combine_models(model1, model2, model1_nograd=True):
    if model1_nograd:
        for layer in model1:
            for k,v in layer._asdict().items():
                v.requires_grad = False
    return model1 + model2


def collect_grads(model):
    grads = [zeros(param.size()) for layer in model for param in layer._asdict().values()]
    ctr = -1
    for layer in model:
        for field in layer._fields:
            ctr += 1
            param = getattr(layer,field)
            if param.requires_grad:
                grads[ctr] += param.grad
                param.grad = None

    return grads

def give_grads(model, grads):
    ctr = -1
    for layer in model:
        for field in layer._fields:
            ctr += 1
            param = getattr(layer,field)
            if param.grad:
                param.grad += grads[ctr]
            else: param.grad = grads[ctr]


##


from torch.nn import Module, Parameter

class Convert2TorchModel(Module):

    def __init__(self, model):
        super(Convert2TorchModel, self).__init__()
        for i,layer in enumerate(model):
            converted = [Parameter(getattr(layer,field)) for field in layer._fields]
            for field, value in zip(layer._fields, converted):
                setattr(self,f'layer{i}_{field}',value)
            setattr(self,f'type{i}',type(layer))
            model[i] = (getattr(self, f'type{layer}'))(converted)

    def forward(self, states, inp):
        model = [(getattr(self,f'type{layer}'))(getattr(self,param) for param in dir(self) if f'layer{layer}' in param)
            for layer in range(len(states))]
        prop_model(model, states, inp)
