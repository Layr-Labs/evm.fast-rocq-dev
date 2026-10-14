From Galette Require Import Base.
From Galette.HOL.src.n_bit Require Import words.
From InitE.InitE Require Import NatE SubmissionModel Challenge.
From InitE.Baseline Require Import Artifacts Submission BitmapImageFacts BaselineCertificate.
From SubmissionLiterals Require Import Values.
Open Scope N_scope.
Definition submission : Submission := baselineSubmission.
Lemma submitted_code_binding : submittedCode = Artifacts.code.
Proof. apply word_list_numeric_ext; vm_compute; reflexivity. Qed.
Definition certificate : Certificate
 (Build_Submission submittedCode claimedScore (anchorPc submission)
  (names submission) (first submission) (extra submission)) claimedScore.
Proof. rewrite submitted_code_binding; exact baseline_certificate. Qed.

