import tempfile
from pathlib import Path
from slovo import DB

def db():
    p=Path(tempfile.mkdtemp())/'test.db'; d=DB(str(p)); d.user(1,'one');d.user(2,'two');return d
def test_roles_and_invite():
    d=db();f=d.create_folder(1,'Words');assert d.role(1,f)=='owner'; t=d.create_invite(1,f,'member');assert d.join(2,t)==f;assert d.role(2,f)=='member';d.revoke_invites(f);d.user(3,'three');assert d.join(3,t) is None
def test_double_answer_is_idempotent():
    d=db();f=d.create_folder(1,'Words');d.add_cards(1,f,[('apple','яблоко')]);c=d.cards(f)[0]['id'];d.save_session('x',1,f,'due',[c]);assert d.answer('x',c,False,'due');assert d.answer('x',c,False,'due') is None
def test_progress_error_then_success_does_not_raise_level():
    d=db();f=d.create_folder(1,'Words');d.add_cards(1,f,[('a','б'),('c','д')]);cards=d.cards(f); a,b=cards[0]['id'],cards[1]['id']
    d.save_session('x',1,f,'due',[a,b,a]);d.answer('x',a,False,'due');d.advance('x');d.answer('x',b,True,'due');d.advance('x')
    # The successful retry after an error stays at level zero.
    d.answer('x',a,True,'due')
    with d.conn() as con:p=con.execute('select level from progress where user_id=1 and card_id=?',(a,)).fetchone()[0]
    assert p == 0

def test_languages_locale_and_profile():
    d=db();d.set_locale(1,'fr');f=d.create_folder(1,'Voyage','fr','es')
    folder=d.folder(1,f)
    assert d.locale(1)=='fr'
    assert (folder['source_lang'],folder['target_lang'])==('fr','es')
    d.add_cards(1,f,[('bonjour','hola')])
    stats=d.profile_stats(1)
    assert stats['folders']==1 and stats['words']==1 and stats['learned']==0

def test_direction_suffix_does_not_change_candidate_mode():
    d=db();f=d.create_folder(1,'Words','en','ru');d.add_cards(1,f,[('one','один')])
    assert len(d.candidates(1,f,'due:rev'))==1
    assert len(d.candidates(1,f,'all:fwd'))==1
