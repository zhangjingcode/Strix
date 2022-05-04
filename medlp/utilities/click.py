from click import Choice, ParamType
from click.types import convert_type

###################### Extension of click ################################


class DynamicTuple(ParamType):
    def __init__(self, input_type):
        self.type = convert_type(input_type)

    @property
    def name(self):
        return "< Dynamic Tuple >"

    def convert(self, value, param, ctx):
        # Hotfix for prompt input
        if isinstance(value, str):
            if "," in value:
                sep = ","
            elif ";" in value:
                sep = ";"
            else:
                sep = " "

            value = value.strip().split(sep)
            value = list(filter(lambda x: x != " ", value))
        elif value is None or value == "":
            return None

        types = (self.type,) * len(value)
        return tuple(ty(x, param, ctx) for ty, x in zip(types, value))


class NumericChoice(Choice):
    def __init__(self, choices, **kwargs):
        self.choicemap = {}
        choicestrs = []
        for i, choice in enumerate(choices, start=1):
            self.choicemap[i] = choice
            if len(choices) > 5:
                choicestrs.append(f"\n\t{i}: {choice}")
            else:
                choicestrs.append(f"{i}: {choice}")

        super().__init__(choicestrs, **kwargs)

    def convert(self, value, param, ctx):
        try:
            return self.choicemap[int(value)]
        except ValueError as e:
            if value in self.choicemap.values():
                return value
            self.fail(
                f"invaid index choice: {value}. Please input integer index or correct value!"
                f"Error msg: {e}"
            )
        except KeyError as e:
            self.fail(
                f"invalid choice: {value}. (choose from {self.choicemap})", param, ctx
            )
