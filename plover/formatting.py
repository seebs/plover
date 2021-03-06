# Copyright (c) 2010-2011 Joshua Harlan Lifton.
# See LICENSE.txt for details.

"""This module converts translations to printable text.

This module defines and implements plover's custom dictionary language.

"""

from os.path import commonprefix
from collections import namedtuple
import re
import string

from plover.orthography import add_suffix


CASE_CAP_FIRST_WORD = 'cap_first_word'
CASE_LOWER = 'lower'
CASE_LOWER_FIRST_CHAR = 'lower_first_char'
CASE_TITLE = 'title'
CASE_UPPER = 'upper'
CASE_UPPER_FIRST_WORD = 'upper_first_word'

SPACE = ' '
META_ATTACH_FLAG = '^'
META_CAPITALIZE = '-|'
META_CARRY_CAPITALIZATION = '~|'
META_COMMAND = 'PLOVER:'
META_COMMAS = (',', ':', ';')
META_GLUE_FLAG = '&'
META_KEY_COMBINATION = '#'
META_LOWER = '>'
META_MODE = 'MODE:'
META_RETRO_CAPITALIZE = '*-|'
META_RETRO_FORMAT = '*('
META_RETRO_LOWER = '*>'
META_RETRO_UPPER = '*<'
META_STOPS = ('.', '!', '?')
META_UPPER = '<'
MODE_CAMEL = 'CAMEL'
MODE_CAPS = 'CAPS'
MODE_LOWER = 'LOWER'
MODE_RESET = 'RESET'
MODE_RESET_CASE = 'RESET_CASE'
MODE_RESET_SPACE = 'RESET_SPACE'
MODE_SET_SPACE = 'SET_SPACE:'
MODE_SNAKE = 'SNAKE'
MODE_TITLE = 'TITLE'

META_ESCAPE = '\\'
RE_META_ESCAPE = '\\\\'
META_START = '{'
META_END = '}'
META_ESC_START = META_ESCAPE + META_START
META_ESC_END = META_ESCAPE + META_END

META_RE = re.compile(r"""(?:%s%s|%s%s|[^%s%s])+ # One or more of anything
                                                # other than unescaped { or }
                                                #
                                              | # or
                                                #
                     %s(?:%s%s|%s%s|[^%s%s])*%s # Anything of the form {X}
                                                # where X doesn't contain
                                                # unescaped { or }
                      """ % (RE_META_ESCAPE, META_START, RE_META_ESCAPE,
                             META_END, META_START, META_END,
                             META_START,
                             RE_META_ESCAPE, META_START, RE_META_ESCAPE,
                             META_END, META_START, META_END,
                             META_END),
                     re.VERBOSE)

# A more human-readable version of the above RE is:
#
# re.compile(r"""(?:\\{|\\}|[^{}])+ # One or more of anything other than
#                                   # unescaped { or }
#                                   #
#                                 | # or
#                                   #
#              {(?:\\{|\\}|[^{}])*} # Anything of the form {X} where X
#                                   # doesn't contain unescaped { or }
#             """, re.VERBOSE)


WORD_RX = re.compile(r'((\d+([.,]\d+)+|\w+[-\w]*|[^\w\s]+)\s*)', re.UNICODE)


class RetroFormatter(object):
    """Helper for iterating over the result of previous translations.

    Support iterating over previous actions, text, fragments of text, or words:

    text     : "Something something, blah! Blah: 45.8... (blah: foo42)   "
    fragments: "__________-----------______------________-------_________"
    words    : "__________---------__----__----__----____-____--_____----"

    """

    FRAGMENT_RX = re.compile(r'\s*[^\s]+\s*|^\s*$')

    def __init__(self, previous_translations):
        self.previous_translations = previous_translations

    def iter_last_actions(self):
        """Iterate over past actions (last first)."""
        for translation in reversed(self.previous_translations):
            for action in reversed(translation.formatting):
                yield action

    def iter_last_fragments(self):
        """Iterate over last text fragments (last first).

        A text fragment is a series of non-whitespace characters
        followed by zero or more trailing whitespace characters.
        """
        replace = 0
        next_action = None
        current_fragment = ''
        for action in self.iter_last_actions():
            part = '' if action.text is None else action.text
            if next_action is not None and \
               next_action.text is not None and \
               not next_action.prev_attach:
                part += next_action.space_char
            if replace:
                # Ignore replaced content.
                if len(part) > replace:
                    part = part[:-replace]
                    replace = 0
                else:
                    replace -= len(part)
                    part = ''
            if part:
                # Find out new complete fragments.
                fragments = self.FRAGMENT_RX.findall(part + current_fragment)
                for f in reversed(fragments[1:]):
                    yield f
                current_fragment = fragments[0]
            replace += len(action.prev_replace)
            next_action = action
        # Don't forget to process the current (first) fragment.
        if not current_fragment.isspace():
            yield current_fragment.lstrip()

    def last_fragments(self, count=1):
        """Return the last <count> text fragments."""
        fragment_list = []
        for fragment in self.iter_last_fragments():
            fragment_list.insert(0, fragment)
            if len(fragment_list) == count:
                break
        return fragment_list

    def iter_last_words(self, strip=False, rx=WORD_RX):
        """Iterate over last words (last first).

        If <strip> is False, then trailing whitespace is included
        as part of each word (useful for calculating position).

        For <strip> to be properly supported when a custom regexp is
        passed as <rx>, then it must include trailing whitespace as
        part of each word.
        """
        for fragment in self.iter_last_fragments():
            # Split each fragment into words.
            for match in reversed(rx.findall(fragment)):
                yield match[0].rstrip() if strip else match[0]

    def last_words(self, count=1, strip=False, rx=WORD_RX):
        """Return the last <count> words."""
        word_list = []
        for w in self.iter_last_words(strip=strip, rx=rx):
            word_list.insert(0, w)
            if len(word_list) == count:
                break
        return word_list

    def last_text(self, size):
        """Return the last <size> characters."""
        text = ''
        if not size:
            return text
        for fragment in self.iter_last_fragments():
            text = fragment + text
            if len(text) >= size:
                break
        return text[-size:]


class _Context(RetroFormatter):
    """Context for formatting translations to actions.

    Keep tracks of previous actions as well as newly translated actions,
    offer helpers for creating new actions and convenient access to past
    actions/text/words.
    """

    def __init__(self, previous_translations, last_action):
        super(_Context, self).__init__(previous_translations)
        assert last_action is not None
        self.last_action = last_action
        self.translated_actions = []

    def new_action(self):
        """Create a new action, only copying global state."""
        return self.last_action.new_state()

    def copy_last_action(self):
        """Create a new action, cloning the last action state."""
        return self.last_action.copy_state()

    def translated(self, action):
        """Mark an action as translated."""
        assert action is not None
        self.translated_actions.append(action)
        self.last_action = action

    def iter_last_actions(self):
        """Custom iterator with support for newly translated actions."""
        for action in reversed(self.translated_actions):
            yield action
        for action in super(_Context, self).iter_last_actions():
            yield action


class Formatter(object):
    """Convert translations into output.

    The main entry point for this class is format, which takes in translations
    to format. Output is sent via an output class passed in through set_output.
    Other than setting the output, the formatter class is stateless.

    The output class can define the following functions, which will be called
    if available:

    send_backspaces -- Takes a number and deletes back that many characters.

    send_string -- Takes a string and prints it verbatim.

    send_key_combination -- Takes a string the dictionary format for specifying
    key combinations and issues them.

    send_engine_command -- Takes a string which names the special command to
    execute.

    """

    output_type = namedtuple(
        'output', ['send_backspaces', 'send_string', 'send_key_combination',
                   'send_engine_command'])

    def __init__(self):
        self.set_output(None)
        self.spaces_after = False
        self.last_output_spaces_after = False
        self.start_capitalized = False
        self.start_attached = False
        self._listeners = set()

    def add_listener(self, callback):
        """Add a listener for translation outputs.

        Arguments:

        callback -- A function that takes: a list of translations to undo, a
        list of new translations to render, and a translation that is the
        context for the new translations.

        """
        self._listeners.add(callback)

    def remove_listener(self, callback):
        """Remove a listener added by add_listener."""
        self._listeners.remove(callback)

    def set_output(self, output):
        """Set the output class."""
        noop = lambda x: None
        output_type = self.output_type
        fields = output_type._fields
        self._output = output_type(*[getattr(output, f, noop) for f in fields])

    def set_space_placement(self, s):
        # Set whether spaces will be inserted
        # before the output or after the output
        self.spaces_after = bool(s == 'After Output')

    def format(self, undo, do, prev):
        """Format the given translations.

        Arguments:

        undo -- A sequence of translations that should be undone. The
        formatting parameter of the translations will be used to undo the
        actions that were taken, if possible.

        do -- The new actions to format. The formatting attribute will be
        filled in with the result.

        prev -- The last translation before the new actions in do. This
        translation's formatting attribute provides the context for the new
        rendered translations. If there is no context then this may be None.

        """
        assert undo or do

        if do:
            last_action = None
            if prev:
                previous_translations = prev
                if prev[-1].formatting:
                    last_action = prev[-1].formatting[-1]
            else:
                previous_translations = []
            if last_action is None:
                # Initial output.
                next_attach = self.start_attached or self.spaces_after
                next_case = CASE_CAP_FIRST_WORD if self.start_capitalized else None
                last_action = _Action(next_attach=next_attach, next_case=next_case)
            ctx = _Context(previous_translations, last_action)
            for t in do:
                if t.english:
                    t.formatting = _translation_to_actions(t.english, ctx)
                else:
                    t.formatting = _raw_to_actions(t.rtfcre[0], ctx)
            new = ctx.translated_actions
        else:
            new = []

        old = [a for t in undo for a in t.formatting]

        # Figure out what really changed.

        min_length = min(len(old), len(new))
        for i in range(min_length):
            if old[i] != new[i]:
                break
        else:
            i = min_length

        if i > 0:
            optimized_away = old[:i]
            old = old[i:]
            new = new[i:]
        else:
            optimized_away = []

        # Notify listeners.

        for callback in self._listeners:
            callback(old, new)

        # Render output.

        if optimized_away:
            last_action = optimized_away[-1]
        elif prev and prev[-1].formatting:
            last_action = prev[-1].formatting[-1]
        else:
            last_action = None

        OutputHelper(self._output, self.last_output_spaces_after,
                     self.spaces_after).render(last_action, old, new)
        self.last_output_spaces_after = self.spaces_after


class TextFormatter(object):
    """Format a series of action into text."""

    def __init__(self, spaces_after):
        self.spaces_after = spaces_after
        # Initial replaced text.
        self.replaced_text = ''
        # New appended text.
        self.appended_text = ''
        self.trailing_space = ''

    def _render_action(self, action):
        if self.spaces_after and self.trailing_space:
            assert self.appended_text.endswith(self.trailing_space)
            self.appended_text = self.appended_text[:-len(self.trailing_space)]
        if action.prev_replace:
            replaced = len(action.prev_replace)
            appended = len(self.appended_text)
            if replaced > appended:
                assert action.prev_replace.endswith(self.appended_text)
                replaced -= appended
                if replaced > len(self.replaced_text):
                    assert action.prev_replace.endswith(self.replaced_text)
                    self.replaced_text = action.prev_replace[:replaced]
                else:
                    assert self.replaced_text.endswith(action.prev_replace)
                    self.replaced_text = self.replaced_text[:-replaced]
                    self.replaced_text += action.prev_replace[:replaced]
                self.appended_text = ''
            else:
                assert self.appended_text.endswith(action.prev_replace)
                self.appended_text = self.appended_text[:-replaced]
        if not action.prev_attach:
            self.appended_text += action.space_char
        self.appended_text += action.text
        if self.spaces_after and not action.next_attach:
            self.appended_text += action.space_char
            self.trailing_space = action.space_char
        else:
            self.trailing_space = ''

    def render(self, action_list, last_action):
        """Render a series of action.

        Note: the function is a generator that yields non-text
        actions (commands, combos, ...) for special processing.
        """
        if self.spaces_after and last_action is not None:
            self.trailing_space = last_action.trailing_space
            self.appended_text = last_action.trailing_space
        for action in action_list:
            if action.text is None:
                yield action
            else:
                self._render_action(action)

    def reset(self, trailing_space):
        """Reset current state (rendered text)."""
        self.replaced_text = ''
        self.appended_text = trailing_space


class OutputHelper(object):
    """A helper class for minimizing the amount of change on output.

    This class figures out the current state, compares it to the new output and
    optimizes away extra backspaces and typing.

    """
    def __init__(self, output, before_spaces_after, after_spaces_after):
        self.output = output
        self.before = TextFormatter(before_spaces_after)
        self.after = TextFormatter(after_spaces_after)

    def flush(self):
        # FIXME:
        # - what about things like emoji zwj sequences?
        # - normalize strings to better handle combining characters?
        #
        # >>> u"C\u0327"
        # 'Ç'
        # >>> len(u"C\u0327")
        # 2
        # >>> len(unicodedata.normalize('NFC', u"C\u0327"))
        # 1
        after = self.after.appended_text
        before = self.after.replaced_text + self.before.appended_text
        common_length = len(commonprefix([before, after]))
        erased = len(before) - common_length
        if erased:
            self.output.send_backspaces(erased)
        appended = after[common_length:]
        if appended:
            self.output.send_string(appended)
        self.before.reset(self.after.trailing_space)
        self.after.reset(self.after.trailing_space)

    def render(self, last_action, undo, do):
        # Render undone actions, ignoring non-text actions.
        for action in self.before.render(undo, last_action):
            pass
        # Render new actions.
        if self.before.replaced_text:
            self.after.appended_text = self.before.replaced_text
        for action in self.after.render(do, last_action):
            self.flush()
            if action.combo:
                self.output.send_key_combination(action.combo)
            elif action.command:
                self.output.send_engine_command(action.command)
        self.flush()


class _Action(object):
    """A hybrid class that stores instructions and resulting state.

    A single translation may be formatted into one or more actions. The
    instructions are used to render the current action and the state is used as
    context to render future translations.

    """

    def __init__(self,
                 # Previous.
                 prev_attach=False, prev_replace='',
                 # Current.
                 glue=False, word=None, orthography=True, space_char=' ',
                 upper_carry=False, case=None, text=None, trailing_space='',
                 combo=None, command=None,
                 # Next.
                 next_attach=False, next_case=None
                ):
        """Initialize a new action.

        Arguments:

        prev_attach -- True if there should be no space between this and the
                       previous action.

        prev_replace -- Text that should be deleted for this action.

        glue -- True if there be no space between this and the next action if
                the next action also has glue set to True.

        word -- The current root word (sans prefix, and un-cased). This is
                context for future actions whose behavior depends on it such as
                suffixes.

        upper_carry -- True if we are uppercasing the current word.

        othography -- True if orthography rules should be applies when adding
                      a suffix to this action.

        space_char -- this character will replace spaces after all other
        formatting has been applied

        case -- an integer to determine which case to output after formatting

        text -- The text that should be rendered for this action.

        trailing_space -- This the space that would be added when rendering
                          up to this action with space placement set to
                          'after output'.

        combo -- The key combo, in plover's key combo language, that should be
                 executed for this action.

        command -- The command that should be executed for this action.

        next_attach -- True if there should be no space between this and the next
                       action.

        next_case -- Case to apply to next action: capitalize/lower/upper...

        """
        # State variables
        self.prev_attach = prev_attach
        self.glue = glue
        self.word = word
        self.upper_carry = upper_carry
        self.orthography = orthography
        self.next_attach = next_attach
        self.next_case = next_case
        # Persistent state variables
        self.space_char = space_char
        self.case = case
        self.trailing_space = trailing_space
        # Instruction variables
        self.prev_replace = prev_replace
        self.text = text
        self.combo = combo
        self.command = command

    def copy_state(self):
        """Clone this action but only clone the state variables."""
        return _Action(
            # Previous.
            prev_attach=self.next_attach,
            # Current.
            case=self.case, glue=self.glue, orthography=self.orthography,
            space_char=self.space_char, upper_carry=self.upper_carry,
            word=self.word, trailing_space=self.trailing_space,
            # Next.
            next_attach=self.next_attach, next_case=self.next_case,
        )

    def new_state(self):
        return _Action(
            # Previous.
            prev_attach=self.next_attach,
            # Current.
            space_char=self.space_char, case=self.case,
            trailing_space=self.trailing_space,
            # Next.
        )

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self == other

    def __str__(self):
        kwargs = [
            '%s=%r' % (k, v)
            for k, v in self.__dict__.items()
            if v != self.DEFAULT.__dict__[k]
        ]
        return 'Action(%s)' % ', '.join(sorted(kwargs))

    def __repr__(self):
        return str(self)

_Action.DEFAULT = _Action()


def _translation_to_actions(translation, ctx):
    """Create actions for a translation.

    Arguments:

    translation -- A string with the translation to render.

    last_action -- The action in whose context this translation is formatted.

    Returns: A list of actions.

    """
    # Reduce the translation to atoms. An atom is an irreducible string that is
    # either entirely a single meta command or entirely text containing no meta
    # commands.
    if translation.isdigit():
        # If a translation is only digits then glue it to neighboring digits.
        atoms = [_glue_translation(translation)]
    else:
        atoms = filter(None, (
            x.strip(' ') for x in META_RE.findall(translation))
        )
    action_list = []
    for atom in atoms:
        action = _atom_to_action(atom, ctx)
        action_list.append(action)
        ctx.translated(action)
    if not action_list:
        action = ctx.copy_last_action()
        action_list = [action]
        ctx.translated(action)
    return action_list


def _raw_to_actions(stroke, ctx):
    """Turn a raw stroke into actions.

    Arguments:

    stroke -- A string representation of the stroke.

    last_action -- The context in which the new actions are created

    Returns: A list of actions.

    """
    # If a raw stroke is composed of digits then remove the dash (if
    # present) and glue it to any neighboring digits. Otherwise, just
    # output the raw stroke as is.
    no_dash = stroke.replace('-', '', 1)
    if no_dash.isdigit():
        return _translation_to_actions(no_dash, ctx)
    action = _Action(text=stroke, word=stroke,
                     case=ctx.last_action.case,
                     prev_attach=ctx.last_action.next_attach,
                     space_char=ctx.last_action.space_char,
                     trailing_space=ctx.last_action.space_char)
    ctx.translated(action)
    return [action]


def _atom_to_action(atom, ctx):
    """Convert an atom into an action.

    Arguments:

    atom -- A string holding an atom. An atom is an irreducible string that is
    either entirely a single meta command or entirely text containing no meta
    commands.

    last_action -- The context in which the new action takes place.

    Returns: An action for the atom.

    """
    meta = _get_meta(atom)
    if meta is not None:
        meta = _unescape_atom(meta)
        if meta in META_COMMAS:
            action = _apply_meta_comma(meta, ctx)
        elif meta in META_STOPS:
            action = _apply_meta_stop(meta, ctx)
        elif meta == META_CAPITALIZE:
            action = _apply_meta_case(CASE_CAP_FIRST_WORD, ctx)
        elif meta == META_LOWER:
            action = _apply_meta_case(CASE_LOWER_FIRST_CHAR, ctx)
        elif meta == META_UPPER:
            action = _apply_meta_case(CASE_UPPER_FIRST_WORD, ctx)
        elif meta == META_RETRO_CAPITALIZE:
            action = _apply_meta_retro_case(CASE_CAP_FIRST_WORD, ctx)
        elif meta == META_RETRO_LOWER:
            action = _apply_meta_retro_case(CASE_LOWER_FIRST_CHAR, ctx)
        elif meta == META_RETRO_UPPER:
            action = _apply_meta_retro_case(CASE_UPPER_FIRST_WORD, ctx)
        elif (meta.startswith(META_CARRY_CAPITALIZATION) or
              meta.startswith(META_ATTACH_FLAG + META_CARRY_CAPITALIZATION)):
            action = _apply_meta_carry_capitalize(meta, ctx)
        elif meta.startswith(META_RETRO_FORMAT):
            action = _apply_meta_currency(meta, ctx)
        elif meta.startswith(META_COMMAND):
            action = _apply_meta_command(meta, ctx)
        elif meta.startswith(META_MODE):
            action = _apply_meta_mode(meta, ctx)
        elif meta.startswith(META_GLUE_FLAG):
            action = _apply_meta_glue(meta, ctx)
        elif (meta.startswith(META_ATTACH_FLAG) or
              meta.endswith(META_ATTACH_FLAG)):
            action = _apply_meta_attach(meta, ctx)
        elif meta.startswith(META_KEY_COMBINATION):
            action = _apply_meta_combo(meta, ctx)
        else:
            action = ctx.new_action()
    else:
        action = ctx.new_action()
        action.text = _unescape_atom(atom)
    # Finalize action's text.
    text = action.text
    if text is not None:
        # Update word.
        if action.word is None:
            last_word = None
            if action.glue and ctx.last_action.glue:
                last_word = ctx.last_action.word
            action.word = _rightmost_word((last_word or '') + text)
        # Apply case.
        case = ctx.last_action.next_case
        if case is None and action.prev_attach and ctx.last_action.upper_carry:
            case = CASE_UPPER_FIRST_WORD
        text = _apply_case(text, case)
        if case == CASE_UPPER_FIRST_WORD:
            action.upper_carry = not ' ' in text
        # Apply mode.
        action.text = _apply_mode(text, action.case, action.space_char,
                                  action.prev_attach, ctx.last_action)
        # Update trailing space.
        action.trailing_space = '' if action.next_attach else action.space_char
    return action


def _apply_meta_attach(meta, ctx):
    action = ctx.new_action()
    begin = meta.startswith(META_ATTACH_FLAG)
    end = meta.endswith(META_ATTACH_FLAG)
    if begin:
        meta = meta[len(META_ATTACH_FLAG):]
        action.prev_attach = True
    if end:
        meta = meta[:-len(META_ATTACH_FLAG)]
        action.next_attach = True
    last_word = ctx.last_action.word or ''
    if not meta:
        # We use an empty connection to indicate a "break" in the
        # application of orthography rules. This allows the
        # stenographer to tell Plover not to auto-correct a word.
        action.orthography = False
    elif (
        last_word and
        not meta.isspace() and
        ctx.last_action.orthography and
        begin and (not end or ' ' in meta)
    ):
        new_word = add_suffix(last_word, meta)
        common_len = len(commonprefix([last_word, new_word]))
        action.prev_replace = last_word[common_len:]
        last_word = last_word[:common_len]
        meta = new_word[common_len:]
    action.text = meta
    if action.prev_attach:
        action.word = _rightmost_word(last_word + meta)
    return action


def _apply_meta_comma(meta, ctx):
    action = ctx.new_action()
    action.text = meta
    action.prev_attach = True
    return action


def _apply_meta_stop(meta, ctx):
    action = ctx.new_action()
    action.prev_attach = True
    action.text = meta
    action.next_case = CASE_CAP_FIRST_WORD
    return action


def _apply_meta_case(meta, ctx):
    action = ctx.copy_last_action()
    action.next_case = meta
    return action


def _apply_meta_retro_case(meta, ctx):
    action = ctx.copy_last_action()
    action.prev_attach = True
    last_words = ctx.last_words(count=1)
    if last_words:
        action.prev_replace = last_words[0]
        action.text = _apply_case(last_words[0], meta)
        if meta == CASE_UPPER_FIRST_WORD:
            action.upper_carry = True
    else:
        action.text = ''
    return action


def _apply_meta_combo(meta, ctx):
    action = ctx.copy_last_action()
    action.combo = meta[len(META_KEY_COMBINATION):]
    return action


def _apply_meta_command(meta, ctx):
    action = ctx.copy_last_action()
    action.command = meta[len(META_COMMAND):]
    return action


def _apply_meta_glue(meta, ctx):
    action = ctx.new_action()
    action.glue = True
    action.text = meta[len(META_GLUE_FLAG):]
    if ctx.last_action.glue:
        action.prev_attach = True
    return action


def _apply_meta_currency(meta, ctx):
    action = ctx.copy_last_action()
    if not meta.endswith(')'):
        return action
    dict_format = meta[len(META_RETRO_FORMAT):-len(')')]
    last_words = ctx.last_words(count=1)
    if not last_words:
        return action
    for cast, fmt in (
        (float, '{:,.2f}'),
        (int,   '{:,}'   ),
    ):
        try:
            cast_input = cast(last_words[0])
        except ValueError:
            pass
        else:
            currency_format = dict_format.replace('c', fmt)
            action.prev_attach = True
            action.prev_replace = last_words[0]
            action.text = currency_format.format(cast_input)
            action.word = None
    return action


def _apply_meta_carry_capitalize(meta, ctx):
    # Meta format: ^~|content^ (attach flags are optional)
    action = ctx.new_action()
    if ctx.last_action.next_case == CASE_CAP_FIRST_WORD:
        action.next_case = CASE_CAP_FIRST_WORD
    begin = meta.startswith(META_ATTACH_FLAG)
    if begin:
        meta = meta[len(META_ATTACH_FLAG):]
        action.prev_attach = True
    meta = meta[len(META_CARRY_CAPITALIZATION):]
    end = meta.endswith(META_ATTACH_FLAG)
    if end:
        meta = meta[:-len(META_ATTACH_FLAG)]
        action.next_attach = True
    if meta or begin or end:
        action.text = meta
    return action


def _apply_meta_mode(meta, ctx):
    """
    command should be:
        CAPS, LOWER, TITLE, CAMEL, SNAKE, RESET_SPACE,
            RESET_CASE, SET_SPACE or RESET

        CAPS: UPPERCASE
        LOWER: lowercase
        TITLE: Title Case
        CAMEL: titleCase, no space, initial lowercase
        SNAKE: Underscore_space
        RESET_SPACE: Space resets to ' '
        RESET_CASE: Reset to normal case
        SET_SPACE:xy: Set space to xy
        RESET: Reset to normal case, space resets to ' '
    """
    action = ctx.copy_last_action()
    command = meta[len(META_MODE):]
    if command == MODE_CAPS:
        action.case = CASE_UPPER
    elif command == MODE_TITLE:
        action.case = CASE_TITLE
    elif command == MODE_LOWER:
        action.case = CASE_LOWER
    elif command == MODE_SNAKE:
        action.space_char = '_'
    elif command == MODE_CAMEL:
        action.case = CASE_TITLE
        action.space_char = ''
        action.next_case = CASE_LOWER_FIRST_CHAR
    elif command == MODE_RESET:
        action.space_char = SPACE
        action.case = None
    elif command == MODE_RESET_SPACE:
        action.space_char = SPACE
    elif command == MODE_RESET_CASE:
        action.case = None
    elif command.startswith(MODE_SET_SPACE):
        action.space_char = command[len(MODE_SET_SPACE):]
    return action


def _apply_case(text, case):
    if case is None:
        return text
    if case == CASE_CAP_FIRST_WORD:
        return _capitalize_first_word(text)
    if case == CASE_LOWER_FIRST_CHAR:
        return _lower_first_character(text)
    if case == CASE_UPPER_FIRST_WORD:
        return _upper_first_word(text)
    raise ValueError('invalid case mode: %s' % case)


def _apply_mode(text, case, space_char, begin, last_action):
    # Should title case be applied to the beginning of the next string?
    lower_title_case = (begin and not
                        last_action.case in (
                            CASE_CAP_FIRST_WORD,
                            CASE_UPPER_FIRST_WORD,
                        ))
    # Apply case, then replace space character
    text = _apply_mode_case(text, case, lower_title_case)
    text = _apply_mode_space_char(text, space_char)
    # Title case is sensitive to lower flag
    if (last_action.next_case == CASE_LOWER_FIRST_CHAR
        and text and case == CASE_TITLE):
        text = _lower_first_character(text)
    return text


def _apply_mode_case(text, case, appended):
    if case is None:
        return text
    if case == CASE_LOWER:
        return text.lower()
    if case == CASE_UPPER:
        return text.upper()
    if case == CASE_TITLE:
        # Do nothing to appended output
        if appended:
            return text
        return _capitalize_all_words(text)
    raise ValueError('invalid case mode: %s' % case)


def _apply_mode_space_char(text, space_char):
    if space_char == SPACE:
        return text
    return text.replace(SPACE, space_char)


def _get_meta(atom):
    """Return the meta command, if any, without surrounding meta markups."""
    if (atom is not None and
        atom.startswith(META_START) and
        atom.endswith(META_END)):
        return atom[len(META_START):-len(META_END)]
    return None


def _glue_translation(s):
    """Mark the given string as a glue stroke."""
    return META_START + META_GLUE_FLAG + s + META_END


def _unescape_atom(atom):
    """Replace escaped meta markups with unescaped meta markups."""
    atom = atom.replace(META_ESC_START, META_START)
    atom = atom.replace(META_ESC_END, META_END)
    return atom


def _capitalize_first_word(s):
    """Capitalize the first letter of s.

    - 'foo bar' -> 'Foo bar'
    - 'STUFF' -> 'STUFF'
    """
    return s[0:1].upper() + s[1:]


def _capitalize_all_words(s):
    """Capitalize each word of s.

    - 'foo bar' -> 'Foo Bar'
    - "O'Something STUFF" -> "O'something Stuff"
    """
    return string.capwords(s, SPACE)


def _lower_first_character(s):
    """Lowercase the first letter of s."""
    return s[0:1].lower() + s[1:]


def _upper_all_words(s):
    """Uppercase the entire s."""
    return s.upper()


def _upper_first_word(s):
    """Uppercase first word of s."""
    m = WORD_RX.match(s)
    if m is None:
        return s
    first_word = m.group()
    return first_word.upper() + s[len(first_word):]


def _rightmost_word(s):
    """Get the rightmost word in s."""
    return s.rpartition(SPACE)[2]
