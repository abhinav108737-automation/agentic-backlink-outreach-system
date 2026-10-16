# Target Repository Structure

apps/desktop/{electron,renderer,preload}
apps/api/src/{api,config,auth,health}
services/core/{campaigns,prospects,qualification,ranking,research,contacts,outreach,replies,negotiation,verification,reporting,learning,optimization,policy,scheduling,audit}
services/providers/{model,search,crawling,seo_metrics,email,vector}
agents/{operator,discovery,qualification,research,contact,outreach,reply,negotiation,verification,reporting}
contracts/{python,generated_ts}
migrations/
evals/
fixtures/
tests/{unit,contract,integration,e2e,security}
docs/{adr,research,phase-reports,runbooks}
infrastructure/{docker,packaging}
