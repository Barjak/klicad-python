# Copyright The KliCAD Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from klipy import KliCAD
from klipy.util import from_mm
from klipy.board_rules import (
    CustomRule,
    CustomRuleConstraint,
    CustomRuleConstraintType,
    CustomRuleDisallowType,
    CustomRuleLayerMode,
    DrcErrorType,
    MinOptMax,
    PresetTrackWidth,
    RuleSeverity,
)

board = KliCAD().get_board()

rules = board.get_design_rules().rules

# Basic rules include the board minimum constraints and overall settings
rules.constraints.min_clearance = from_mm(0.325)
rules.constraints.min_track_width = from_mm(0.178)

# You can also change rule severities
rules.severities[DrcErrorType.DRCET_SHORTING_ITEMS] = RuleSeverity.RS_WARNING
rules.severities[DrcErrorType.DRCET_LENGTH_OUT_OF_RANGE] = RuleSeverity.RS_ERROR

# Track and via size definitions are also part of the board design rules
rules.predefined_sizes.tracks.append(PresetTrackWidth.from_width(from_mm(0.275)))

board.set_design_rules(rules)

custom_rules = board.get_custom_design_rules().rules

# Each CustomRule object maps to a rule entry in the kicad_dru file

cr = CustomRule()
cr.name = "my rule"
cr.layer_mode = CustomRuleLayerMode.CRLM_OUTER  # optional, omit for "all layers"
cr.comments = "A rule to do something special"  # optional

# The rule condition is an expression in KliCAD's custom rule language

cr.condition = "A.NetClass == 'MyClass' && A.Type == 'Track'"

# A CustomRule can have more than one constraint

c1 = CustomRuleConstraint.from_type(CustomRuleConstraintType.CRCT_TRACK_WIDTH)
c1.numeric = MinOptMax(min=from_mm(0.25), max=from_mm(0.75))

c2 = CustomRuleConstraint.from_type(CustomRuleConstraintType.CRCT_DISALLOW)
c2.disallow.types.append(CustomRuleDisallowType.CRDT_PADS)

cr.constraints.extend([c1, c2])
custom_rules.append(cr)

board.set_custom_design_rules(custom_rules)
