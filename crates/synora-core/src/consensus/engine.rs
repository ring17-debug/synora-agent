//! Deterministic consensus state machine.
//!
//! This is the first executable consensus layer for Synora.
//!
//! Flow: Proposal -> Prevote -> 2/3 quorum -> Precommit -> 2/3 quorum -> Commit decision.
//!
//! The consensus engine advances deterministically through these phases.
//! A proposal is followed by prevotes, then precommits after quorum is
//! reached, and finally a commit decision after precommit quorum.
//!
//! Networking, persistence, signatures, and block execution are intentionally
//! kept outside this module.

use super::vote::{Vote, VoteSet, VoteType};
use super::{
    BlockProposal, ConsensusError, ConsensusRound, ConsensusValidator, ValidatorId, ValidatorSet,
};
use crate::hash::Hash;

/// Current phase of the consensus state machine.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConsensusPhase {
    /// Waiting for a valid block proposal.
    Propose,

    /// Proposal accepted, collecting prevotes.
    Prevote,

    /// Prevote quorum reached, collecting precommits.
    Precommit,

    /// Precommit quorum reached.
    Committed,
}

/// Result of a successful consensus decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommitDecision {
    pub height: u64,
    pub round: u64,
    pub block_hash: Hash,
}

impl CommitDecision {
    pub fn new(height: u64, round: u64, block_hash: Hash) -> Self {
        Self {
            height,
            round,
            block_hash,
        }
    }
}

/// Consensus state for one height.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConsensusEngine {
    validator_set: ValidatorSet,
    round: ConsensusRound,
    phase: ConsensusPhase,
    proposal: Option<BlockProposal>,
    prevotes: VoteSet,
    precommits: VoteSet,
    decision: Option<CommitDecision>,
}

impl ConsensusEngine {
    /// Creates a new consensus engine for a block height.
    pub fn new(validator_set: ValidatorSet, height: u64) -> Self {
        Self::new_with_round(validator_set, height, 0)
    }

    /// Creates a new consensus engine for an explicit height and round.
    pub fn new_with_round(validator_set: ValidatorSet, height: u64, round: u64) -> Self {
        Self {
            validator_set,
            round: ConsensusRound::new(height, round),
            phase: ConsensusPhase::Propose,
            proposal: None,
            prevotes: VoteSet::new(height, round, VoteType::Prevote),
            precommits: VoteSet::new(height, round, VoteType::Precommit),
            decision: None,
        }
    }

    pub fn validator_set(&self) -> &ValidatorSet {
        &self.validator_set
    }

    pub fn round(&self) -> ConsensusRound {
        self.round
    }

    pub fn phase(&self) -> ConsensusPhase {
        self.phase
    }

    pub fn proposal(&self) -> Option<&BlockProposal> {
        self.proposal.as_ref()
    }

    pub fn prevotes(&self) -> &VoteSet {
        &self.prevotes
    }

    pub fn precommits(&self) -> &VoteSet {
        &self.precommits
    }

    pub fn decision(&self) -> Option<CommitDecision> {
        self.decision
    }

    pub fn is_committed(&self) -> bool {
        self.phase == ConsensusPhase::Committed
    }

    /// Validates and accepts a block proposal.
    pub fn submit_proposal(&mut self, proposal: BlockProposal) -> Result<(), ConsensusError> {
        if self.is_committed() {
            return Err(ConsensusError::AlreadyCommitted);
        }

        if proposal.height != self.round.height {
            return Err(ConsensusError::InvalidProposalHeight);
        }

        if proposal.round != self.round.round {
            return Err(ConsensusError::InvalidProposalRound);
        }

        ConsensusValidator::new(self.validator_set.clone()).validate_proposal(&proposal)?;

        if self.proposal.is_some() {
            return Err(ConsensusError::DuplicateProposal);
        }

        self.proposal = Some(proposal);
        self.phase = ConsensusPhase::Prevote;

        Ok(())
    }

    /// Submit a prevote.
    ///
    /// A prevote is only accepted after a proposal has been accepted.
    pub fn submit_prevote(
        &mut self,
        validator: ValidatorId,
    ) -> Result<ConsensusPhase, ConsensusError> {
        let proposal_hash = self
            .proposal
            .as_ref()
            .ok_or(ConsensusError::ProposalRequired)?
            .block_hash;

        self.submit_vote(Vote::new(
            self.round.height,
            self.round.round,
            proposal_hash,
            validator,
            VoteType::Prevote,
        ))
    }

    /// Submit a precommit.
    ///
    /// Precommit is only accepted after prevote quorum for the same block.
    pub fn submit_precommit(
        &mut self,
        validator: ValidatorId,
    ) -> Result<ConsensusPhase, ConsensusError> {
        if self.phase != ConsensusPhase::Precommit {
            if self.phase == ConsensusPhase::Prevote {
                return Err(ConsensusError::PrevoteQuorumRequired);
            }

            return Err(ConsensusError::InvalidConsensusPhase);
        }

        let proposal_hash = self
            .proposal
            .as_ref()
            .ok_or(ConsensusError::ProposalRequired)?
            .block_hash;

        self.submit_vote(Vote::new(
            self.round.height,
            self.round.round,
            proposal_hash,
            validator,
            VoteType::Precommit,
        ))
    }

    /// Submit a complete vote payload.
    pub fn submit_vote(&mut self, vote: Vote) -> Result<ConsensusPhase, ConsensusError> {
        if self.is_committed() {
            return Err(ConsensusError::AlreadyCommitted);
        }

        if vote.height != self.round.height {
            return Err(ConsensusError::InvalidVoteHeight);
        }

        if vote.round != self.round.round {
            return Err(ConsensusError::InvalidVoteRound);
        }

        let proposal = self
            .proposal
            .as_ref()
            .ok_or(ConsensusError::ProposalRequired)?;

        if vote.block_hash != proposal.block_hash {
            return Err(ConsensusError::InvalidVoteBlock);
        }

        match vote.vote_type {
            VoteType::Prevote => {
                if self.phase != ConsensusPhase::Prevote {
                    return Err(ConsensusError::InvalidConsensusPhase);
                }

                self.prevotes.insert(vote, &self.validator_set)?;

                if self.prevotes.has_quorum(&self.validator_set) {
                    self.phase = ConsensusPhase::Precommit;
                }
            }

            VoteType::Precommit => {
                if self.phase != ConsensusPhase::Precommit {
                    return Err(ConsensusError::PrevoteQuorumRequired);
                }

                self.precommits.insert(vote, &self.validator_set)?;

                if self.precommits.has_quorum(&self.validator_set) {
                    self.commit();
                }
            }
        }

        Ok(self.phase)
    }

    /// Advance to a new round at the same height.
    ///
    /// A committed engine cannot be moved to another round.
    pub fn advance_round(&mut self) -> Result<ConsensusRound, ConsensusError> {
        if self.is_committed() {
            return Err(ConsensusError::AlreadyCommitted);
        }

        self.round.round = self.round.round.saturating_add(1);

        self.phase = ConsensusPhase::Propose;
        self.proposal = None;
        self.prevotes = VoteSet::new(self.round.height, self.round.round, VoteType::Prevote);
        self.precommits = VoteSet::new(self.round.height, self.round.round, VoteType::Precommit);
        self.decision = None;

        Ok(self.round)
    }

    fn commit(&mut self) {
        let proposal = self
            .proposal
            .as_ref()
            .expect("proposal must exist before commit");

        self.decision = Some(CommitDecision::new(
            proposal.height,
            proposal.round,
            proposal.block_hash,
        ));

        self.phase = ConsensusPhase::Committed;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::block::Block;

    fn validator(value: u8) -> ValidatorId {
        [value; 20]
    }

    fn validators() -> ValidatorSet {
        ValidatorSet::new(vec![
            super::super::Validator::new(validator(1)),
            super::super::Validator::new(validator(2)),
            super::super::Validator::new(validator(3)),
        ])
        .expect("validator set should be valid")
    }

    fn proposal() -> BlockProposal {
        let block = Block::genesis(1, 1_700_000_000);

        BlockProposal::new(&block, 0, validator(1))
    }

    #[test]
    fn engine_starts_in_propose_phase() {
        let engine = ConsensusEngine::new(validators(), 0);

        assert_eq!(engine.phase(), ConsensusPhase::Propose);
        assert_eq!(engine.round(), ConsensusRound::new(0, 0));
        assert!(!engine.is_committed());
    }

    #[test]
    fn valid_proposal_moves_engine_to_prevote() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine
            .submit_proposal(proposal())
            .expect("proposal should be accepted");

        assert_eq!(engine.phase(), ConsensusPhase::Prevote);
        assert!(engine.proposal().is_some());
    }

    #[test]
    fn invalid_proposer_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        let mut invalid = proposal();
        invalid.proposer = validator(2);

        assert_eq!(
            engine.submit_proposal(invalid),
            Err(ConsensusError::InvalidProposer)
        );
    }

    #[test]
    fn duplicate_proposal_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        assert_eq!(
            engine.submit_proposal(proposal()),
            Err(ConsensusError::DuplicateProposal)
        );
    }

    #[test]
    fn prevote_requires_proposal() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        assert_eq!(
            engine.submit_prevote(validator(1)),
            Err(ConsensusError::ProposalRequired)
        );
    }

    #[test]
    fn two_thirds_prevotes_move_to_precommit() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        assert_eq!(
            engine.submit_prevote(validator(1)).unwrap(),
            ConsensusPhase::Prevote
        );

        assert_eq!(
            engine.submit_prevote(validator(2)).unwrap(),
            ConsensusPhase::Precommit
        );
    }

    #[test]
    fn precommit_requires_prevote_quorum() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        assert_eq!(
            engine.submit_precommit(validator(1)),
            Err(ConsensusError::PrevoteQuorumRequired)
        );
    }

    #[test]
    fn two_thirds_precommits_commit_block() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        let proposal = proposal();
        let expected_hash = proposal.block_hash;

        engine.submit_proposal(proposal).unwrap();

        engine.submit_prevote(validator(1)).unwrap();

        engine.submit_prevote(validator(2)).unwrap();

        assert_eq!(engine.phase(), ConsensusPhase::Precommit);

        engine.submit_precommit(validator(1)).unwrap();

        assert_eq!(engine.phase(), ConsensusPhase::Precommit);

        engine.submit_precommit(validator(2)).unwrap();

        assert_eq!(engine.phase(), ConsensusPhase::Committed);

        assert!(engine.is_committed());

        assert_eq!(
            engine.decision(),
            Some(CommitDecision::new(0, 0, expected_hash,))
        );
    }

    #[test]
    fn duplicate_prevote_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        engine.submit_prevote(validator(1)).unwrap();

        assert_eq!(
            engine.submit_prevote(validator(1)),
            Err(ConsensusError::DuplicateVote)
        );
    }

    #[test]
    fn unknown_validator_cannot_vote() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        assert_eq!(
            engine.submit_prevote(validator(9)),
            Err(ConsensusError::UnknownValidator)
        );
    }

    #[test]
    fn engine_can_advance_round() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        engine.advance_round().expect("round should advance");

        assert_eq!(engine.round(), ConsensusRound::new(0, 1));
        assert_eq!(engine.phase(), ConsensusPhase::Propose);
        assert!(engine.proposal().is_none());
        assert!(engine.prevotes().is_empty());
        assert!(engine.precommits().is_empty());
    }

    #[test]
    fn committed_engine_cannot_advance_round() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        engine.submit_proposal(proposal()).unwrap();

        engine.submit_prevote(validator(1)).unwrap();

        engine.submit_prevote(validator(2)).unwrap();

        engine.submit_precommit(validator(1)).unwrap();

        engine.submit_precommit(validator(2)).unwrap();

        assert_eq!(
            engine.advance_round(),
            Err(ConsensusError::AlreadyCommitted)
        );
    }

    #[test]
    fn wrong_height_proposal_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 1);

        let mut proposal = proposal();
        proposal.height = 0;

        assert_eq!(
            engine.submit_proposal(proposal),
            Err(ConsensusError::InvalidProposalHeight)
        );
    }

    #[test]
    fn wrong_round_proposal_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        let mut proposal = proposal();
        proposal.round = 1;

        assert_eq!(
            engine.submit_proposal(proposal),
            Err(ConsensusError::InvalidProposalRound)
        );
    }

    #[test]
    fn conflicting_block_vote_is_rejected() {
        let mut engine = ConsensusEngine::new(validators(), 0);

        let proposal = proposal();
        let block_hash = proposal.block_hash;

        engine.submit_proposal(proposal).unwrap();

        let conflicting = Vote::new(0, 0, [99u8; 32], validator(1), VoteType::Prevote);

        assert_ne!(conflicting.block_hash, block_hash);

        assert_eq!(
            engine.submit_vote(conflicting),
            Err(ConsensusError::InvalidVoteBlock)
        );
    }
}
